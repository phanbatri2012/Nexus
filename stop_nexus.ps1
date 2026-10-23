[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 25
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lifecycle_admin.ps1")

if (-not (Test-NexusAdministrator)) {
    $forwardArguments = @()
    if ($PSBoundParameters.ContainsKey("TimeoutSeconds")) {
        $forwardArguments += @("-TimeoutSeconds", [string]$TimeoutSeconds)
    }
    exit (Invoke-NexusElevatedScript -ScriptPath $PSCommandPath -Arguments $forwardArguments)
}

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")
$legacyProjectRoot = Join-Path $projectRoot "Tool-auto-login-GPT"
$dataRoot = Join-Path $projectRoot "data"
$chromeUserDataRoot = Join-Path $dataRoot "chrome_user_data"
$legacyChromeUserDataRoot = Join-Path $legacyProjectRoot "data\chrome_user_data"
$museServiceStateFile = Join-Path $dataRoot "muse_studio_service.json"
$servicePorts = @(8080, 5173, 8011)
$omniVoiceRoot = if ($env:NEXUS_OMNIVOICE_ROOT) {
    [System.IO.Path]::GetFullPath($env:NEXUS_OMNIVOICE_ROOT).TrimEnd("\")
}
elseif ($env:AUTO_YT_OMNIVOICE_ROOT) {
    [System.IO.Path]::GetFullPath($env:AUTO_YT_OMNIVOICE_ROOT).TrimEnd("\")
}
else {
    [System.IO.Path]::GetFullPath(
        (Join-Path (Split-Path $projectRoot -Parent) "omnivoice")
    ).TrimEnd("\")
}

function Write-Step {
    param([string]$Message)
    Write-Host "[Nexus Studio Engine] $Message" -ForegroundColor Cyan
}

function Get-RestartMaintenanceStatus {
    $pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "Cannot verify GPM jobs because the project Python environment is missing."
    }

    $previousPythonPath = $env:PYTHONPATH
    $previousPythonIoEncoding = $env:PYTHONIOENCODING
    try {
        $env:PYTHONPATH = Join-Path $projectRoot "src"
        $env:PYTHONIOENCODING = "utf-8"
        $statusJson = & $pythonPath -m nexus_engine.services.maintenance_guard
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($statusJson)) {
            throw "Maintenance guard returned no status."
        }
        return ($statusJson | ConvertFrom-Json)
    }
    catch {
        throw "Cannot verify whether GPM channel jobs are idle: $($_.Exception.Message)"
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }
}

function Get-ProcessSnapshot {
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
}

function Test-NexusProcess {
    param($Process)

    if (-not $Process) {
        return $false
    }
    $procName = [string]$Process.Name
    if ($procName -in @("chrome.exe", "msedge.exe", "browser.exe", "brave.exe", "firefox.exe", "opera.exe", "explorer.exe")) {
        if ($procName -eq "chrome.exe") {
            return (Test-NexusChromeProcess $Process)
        }
        return $false
    }
    $identity = "$($Process.ExecutablePath)`n$($Process.CommandLine)"
    if ($identity.IndexOf($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if ($omniVoiceRoot -and $identity.IndexOf($omniVoiceRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if (
        $identity -match "(?:^|\s)nexus_engine\.(?:main|services\.(?:chatgpt_browser_service|google_flow_browser_service))(?:\s|:|$)" -or
        $identity -match "(?:^|\s)auto_yt\.(?:main|services\.(?:chatgpt_browser_service|google_flow_browser_service))(?:\s|:|$)"
    ) {
        return $true
    }
    return $false
}

function Test-NexusChromeProcess {
    param($Process)

    if (-not $Process -or $Process.Name -ne "chrome.exe") {
        return $false
    }
    $cmd = [string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($cmd)) {
        return $false
    }
    if ($cmd.IndexOf($chromeUserDataRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if ($cmd.IndexOf($legacyChromeUserDataRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    return $false
}

function Get-ProjectAncestorId {
    param(
        [int]$ProcessId,
        [object[]]$Processes
    )

    $byId = @{}
    foreach ($process in $Processes) {
        $byId[[int]$process.ProcessId] = $process
    }

    $currentId = $ProcessId
    $visited = @{}
    while ($currentId -gt 0 -and -not $visited.ContainsKey($currentId)) {
        $visited[$currentId] = $true
        $current = $byId[$currentId]
        if (-not $current) {
            break
        }
        $currName = [string]$current.Name
        if ($currName -in @("explorer.exe", "WindowsTerminal.exe", "conhost.exe", "chrome.exe", "msedge.exe", "browser.exe", "brave.exe", "firefox.exe", "opera.exe")) {
            break
        }
        if (Test-NexusProcess $current) {
            return [int]$current.ProcessId
        }
        $currentId = [int]$current.ParentProcessId
    }
    return 0
}

function Get-ProcessTreeIds {
    param(
        [int]$RootId,
        [object[]]$Processes
    )

    $result = New-Object System.Collections.Generic.List[int]
    $pending = New-Object System.Collections.Generic.Queue[int]
    $pending.Enqueue($RootId)
    while ($pending.Count -gt 0) {
        $currentId = $pending.Dequeue()
        if ($result.Contains($currentId)) {
            continue
        }
        $result.Add($currentId)
        foreach ($child in $Processes | Where-Object { [int]$_.ParentProcessId -eq $currentId }) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($result)
}

function Get-ListeningProcessIds {
    param([int]$Port)

    try {
        return @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }
    catch {
        return @()
    }
}

function Get-OwnedMuseServiceInfo {
    param([object[]]$Processes)

    if (-not (Test-Path -LiteralPath $museServiceStateFile -PathType Leaf)) {
        return $null
    }
    try {
        $state = Get-Content -LiteralPath $museServiceStateFile -Raw | ConvertFrom-Json
        if (-not $state.owned -or [int]$state.pid -le 0 -or [string]::IsNullOrWhiteSpace([string]$state.studio_path)) {
            return $null
        }
        $studioPath = [System.IO.Path]::GetFullPath([string]$state.studio_path).TrimEnd("\")
        $expectedMain = Join-Path $studioPath "main.py"
        $process = $Processes | Where-Object { [int]$_.ProcessId -eq [int]$state.pid } | Select-Object -First 1
        if (-not $process) {
            return $null
        }
        $commandLine = [string]$process.CommandLine
        if (
            $commandLine.IndexOf("--muse-service", [System.StringComparison]::OrdinalIgnoreCase) -lt 0 -or
            $commandLine.IndexOf($expectedMain, [System.StringComparison]::OrdinalIgnoreCase) -lt 0
        ) {
            Write-Warning "Muse service marker does not match PID $($state.pid); the process will not be stopped."
            return $null
        }
        return [pscustomobject]@{
            process_id = [int]$state.pid
            profile_root = Join-Path $studioPath "chrome_user_data\muse_profiles"
        }
    }
    catch {
        Write-Warning "Muse service marker is invalid; no external process will be stopped."
        return $null
    }
}

function Request-BrowserServiceStop {
    param([string]$ServiceName)

    $stopFile = Join-Path $dataRoot "$ServiceName.stop.json"
    $stateFile = Join-Path $dataRoot "$ServiceName.json"
    if (Test-Path -LiteralPath $stateFile -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            $payload = @{
                instance_id = [string]$state.instance_id
                requested_at = (Get-Date).ToUniversalTime().ToString("o")
            } | ConvertTo-Json
            Set-Content -LiteralPath $stopFile -Value $payload -Encoding UTF8
        }
        catch {
            # Non-blocking best effort
        }
    }
}

$createdNew = $false
$stopMutex = New-Object System.Threading.Mutex($true, "Local\NexusStop", [ref]$createdNew)
if (-not $createdNew) {
    Write-Host "Another Nexus Studio Engine stop is already in progress." -ForegroundColor Yellow
    $stopMutex.Dispose()
    exit 0
}

try {
    Write-Step "Analyzing running Nexus Studio Engine services and processes..."


    $processes = Get-ProcessSnapshot
    $rootIds = New-Object System.Collections.Generic.HashSet[int]
    $museRootIds = New-Object System.Collections.Generic.HashSet[int]
    $ownedMuseService = Get-OwnedMuseServiceInfo $processes
    if ($ownedMuseService) {
        $null = $rootIds.Add([int]$ownedMuseService.process_id)
        $null = $museRootIds.Add([int]$ownedMuseService.process_id)
    }

    # 1. Listeners on service ports: 8080 (backend), 5173 (frontend), 8011 (omnivoice)
    foreach ($port in $servicePorts) {
        foreach ($listenerId in Get-ListeningProcessIds $port) {
            $ancestorId = Get-ProjectAncestorId ([int]$listenerId) $processes
            if ($ancestorId -gt 0) {
                $null = $rootIds.Add($ancestorId)
            }
            else {
                $proc = $processes | Where-Object { [int]$_.ProcessId -eq [int]$listenerId } | Select-Object -First 1
                if ($proc -and (Test-NexusProcess $proc)) {
                    $null = $rootIds.Add([int]$listenerId)
                }
                else {
                    Write-Warning "Port $port is used by an external application (PID: $listenerId); it will not be stopped."
                }
            }
        }
    }

    # 2. Named project processes
    foreach ($process in $processes) {
        $cmd = [string]$process.CommandLine
        $isProject = Test-NexusProcess $process

        if ($isProject) {
            if (
                $cmd -match "nexus_engine\.main:app" -or
                $cmd -match "nexus_engine\.services\.chatgpt_browser_service" -or
                $cmd -match "nexus_engine\.services\.google_flow_browser_service" -or
                $cmd -match "auto_yt\.main:app" -or
                $cmd -match "auto_yt\.services\.chatgpt_browser_service" -or
                $cmd -match "auto_yt\.services\.google_flow_browser_service" -or
                $cmd -match "api_server:app" -or
                $cmd -match "[\\/]vite(?:\.js)?(?:\s|$)" -or
                $cmd -match "npm(?:\.cmd)?\s+run\s+dev" -or
                $cmd -match "playwright"
            ) {
                $null = $rootIds.Add([int]$process.ProcessId)
            }
        }
    }

    # 3. Dedicated Chrome processes with Nexus Studio Engine profiles
    foreach ($process in $processes) {
        if (Test-NexusChromeProcess $process) {
            $null = $rootIds.Add([int]$process.ProcessId)
        }
    }

    if ($rootIds.Count -eq 0) {
        if (Test-Path -LiteralPath $museServiceStateFile -PathType Leaf) {
            Remove-Item -LiteralPath $museServiceStateFile -Force -ErrorAction SilentlyContinue
        }
        Write-Host "Nexus Studio Engine is not running." -ForegroundColor Yellow
        exit 0
    }

    if ($env:NEXUS_REQUIRE_GPM_IDLE -eq "1" -or $env:AUTOYT_REQUIRE_GPM_IDLE -eq "1" -or $env:NEXUS_REQUIRE_IDLE -eq "1" -or $env:AUTOYT_REQUIRE_IDLE -eq "1") {
        $maintenanceStatus = Get-RestartMaintenanceStatus
        if ($maintenanceStatus -and -not $maintenanceStatus.safe_to_restart) {
            Write-Host "`n[Nexus Studio Engine] [CANH BAO AN TOAN] He thong dang co $($maintenanceStatus.total_blocking_jobs) tac vu dang chay:" -ForegroundColor Yellow
            foreach ($reason in @($maintenanceStatus.reasons)) {
                Write-Host "  - $reason" -ForegroundColor Red
            }
            foreach ($job in @($maintenanceStatus.blocking_jobs) | Select-Object -First 5) {
                $desc = if ($job.PSObject.Properties['title'] -and $job.title) {
                    [string]$job.title
                } elseif ($job.PSObject.Properties['video_title'] -and $job.video_title) {
                    [string]$job.video_title
                } elseif ($job.PSObject.Properties['id']) {
                    "ID: $($job.id)"
                } else {
                    "Unknown job"
                }
                $type = if ($job.PSObject.Properties['type'] -and $job.type) { [string]$job.type } else { "job" }
                $statusVal = if ($job.PSObject.Properties['status'] -and $job.status) { [string]$job.status } else { "running" }
                Write-Host "    * [$type] $($statusVal): $desc" -ForegroundColor Yellow
            }
            Write-Host ""
            throw "Nexus Studio Engine restart/stop bi chan an toan vi he thong dang thuc hien tac vu quan trong (render, upload YouTube, dang bai, hoac GPM). Vui long doi cac job tren hoan tat truoc khi restart!"
        }
    }
    
    Request-BrowserServiceStop "chatgpt_browser_service"
    Request-BrowserServiceStop "google_flow_browser_service"


    $targetIds = New-Object System.Collections.Generic.HashSet[int]
    foreach ($rootId in $rootIds) {
        $isMuseTree = $museRootIds.Contains([int]$rootId)
        foreach ($processId in Get-ProcessTreeIds $rootId $processes) {
            $proc = $processes | Where-Object { [int]$_.ProcessId -eq $processId } | Select-Object -First 1
            if ($proc) {
                $procName = [string]$proc.Name
                if ($procName -in @("chrome.exe", "msedge.exe", "browser.exe", "brave.exe", "firefox.exe", "opera.exe", "explorer.exe")) {
                    if ($procName -eq "explorer.exe") {
                        continue
                    }
                    if (-not $isMuseTree -and ($procName -ne "chrome.exe" -or -not (Test-NexusChromeProcess $proc))) {
                        continue
                    }
                }
            }
            $null = $targetIds.Add($processId)
        }
    }

    Write-Step "Stopping backend, frontend, OmniVoice, owned Muse2API, and project browser services ($($targetIds.Count) processes)..."
    foreach ($processId in @($targetIds) | Sort-Object -Descending) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $activePortListeners = @()
    do {
        Start-Sleep -Milliseconds 150
        $activePortListeners = @()
        foreach ($port in $servicePorts) {
            $listeners = @(Get-ListeningProcessIds $port)
            if ($listeners.Length -gt 0) {
                $activePortListeners += $listeners
            }
        }
        if ($activePortListeners.Length -eq 0) {
            break
        }
    } while ((Get-Date) -lt $deadline)

    if ($activePortListeners.Length -gt 0) {
        $snapshot = Get-ProcessSnapshot
        foreach ($port in $servicePorts) {
            foreach ($listenerId in @(Get-ListeningProcessIds $port)) {
                $proc = $snapshot | Where-Object { [int]$_.ProcessId -eq [int]$listenerId } | Select-Object -First 1
                if ($proc -and (Test-NexusProcess $proc)) {
                    Stop-Process -Id $listenerId -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }

    Write-Step "Cleaning up state markers and Chrome lock files..."
    $stateFiles = @(
        (Join-Path $dataRoot "chatgpt_browser_service.json"),
        (Join-Path $dataRoot "chatgpt_browser_service.stop.json"),
        (Join-Path $dataRoot "google_flow_browser_service.json"),
        (Join-Path $dataRoot "google_flow_browser_service.stop.json"),
        $museServiceStateFile,
        (Join-Path $omniVoiceRoot "data\worker.pid")
    )
    foreach ($sf in $stateFiles) {
        if (Test-Path -LiteralPath $sf -PathType Leaf) {
            Remove-Item -LiteralPath $sf -Force -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path -LiteralPath $chromeUserDataRoot -PathType Container) {
        Get-ChildItem -LiteralPath $chromeUserDataRoot -Filter "SingletonLock" -Recurse -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }

    $frontendNodeModules = Join-Path (Join-Path $projectRoot "frontend") "node_modules"
    if (Test-Path -LiteralPath $frontendNodeModules -PathType Container) {
        Get-ChildItem -LiteralPath $frontendNodeModules -Directory -Filter ".vite_stale_*" -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host "[Nexus Studio Engine] All services, including Radar and Trust Builder schedulers, have stopped cleanly. Ports 8080, 5173, and 8011 are free." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "Nexus Studio Engine stop failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    $stopMutex.ReleaseMutex()
    $stopMutex.Dispose()
}
