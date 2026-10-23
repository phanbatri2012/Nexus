[CmdletBinding()]
param(
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd("\")
$legacyRoot = Join-Path $repoRoot "Tool-auto-login-GPT"
$canonicalData = Join-Path $repoRoot "data"
$legacyData = Join-Path $legacyRoot "data"
$legacyPython = Join-Path $legacyRoot ".venv\Scripts\python.exe"
$canonicalPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$canonicalNodeModules = Join-Path $repoRoot "frontend\node_modules"
$migrationHelper = Join-Path $PSScriptRoot "migrate_nexus_runtime_paths.py"

function Assert-PathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd("\")
    if (-not $resolvedPath.StartsWith("$resolvedRoot\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the expected root: $resolvedPath"
    }
    return $resolvedPath
}

function Get-MaintenanceStatus {
    $canonicalDatabase = Join-Path $canonicalData "database.db"
    $useCanonicalGuard = Test-Path -LiteralPath $canonicalDatabase -PathType Leaf
    $guardPython = if ($useCanonicalGuard) { $canonicalPython } else { $legacyPython }
    $guardSource = if ($useCanonicalGuard) { Join-Path $repoRoot "src" } else { Join-Path $legacyRoot "src" }
    $guardModule = if ($useCanonicalGuard) { "nexus_engine.services.maintenance_guard" } else { "auto_yt.services.maintenance_guard" }
    if (-not (Test-Path -LiteralPath $guardPython -PathType Leaf)) {
        throw "Python runtime is required to verify maintenance status."
    }
    $previousPythonPath = $env:PYTHONPATH
    $previousEncoding = $env:PYTHONIOENCODING
    $previousNexusDatabase = $env:NEXUS_DB_PATH
    $previousLegacyDatabase = $env:AUTO_YT_DB_PATH
    try {
        $env:PYTHONPATH = $guardSource
        $env:PYTHONIOENCODING = "utf-8"
        if ($useCanonicalGuard) {
            $env:NEXUS_DB_PATH = $canonicalDatabase
            $env:AUTO_YT_DB_PATH = $canonicalDatabase
        }
        $json = & $guardPython -m $guardModule
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($json)) {
            throw "Maintenance guard returned no usable status."
        }
        return ($json | ConvertFrom-Json)
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONIOENCODING = $previousEncoding
        $env:NEXUS_DB_PATH = $previousNexusDatabase
        $env:AUTO_YT_DB_PATH = $previousLegacyDatabase
    }
}

function Assert-CoreServicesStopped {
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort 8080, 5173, 8011 -ErrorAction SilentlyContinue
    )
    if ($listeners.Count -gt 0) {
        $ports = ($listeners | Select-Object -ExpandProperty LocalPort -Unique | Sort-Object) -join ", "
        throw "Core services are still listening on ports $ports. Run the legacy safe stop before migration."
    }
}

function Move-ToQuarantine {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$QuarantineRoot,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Manifest
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }
    $resolvedSource = Assert-PathWithin -Path $Source -Root $repoRoot
    $resolvedQuarantine = Assert-PathWithin -Path $QuarantineRoot -Root $repoRoot
    $leaf = Split-Path $resolvedSource -Leaf
    $destination = Join-Path $resolvedQuarantine $leaf
    $counter = 1
    while (Test-Path -LiteralPath $destination) {
        $destination = Join-Path $resolvedQuarantine "$leaf-$counter"
        $counter++
    }
    Move-Item -LiteralPath $resolvedSource -Destination $destination
    $Manifest.Add([pscustomobject]@{
        original_path = $resolvedSource
        quarantine_path = $destination
    })
}

if (-not (Test-Path -LiteralPath $legacyRoot -PathType Container)) {
    throw "Legacy layout is not present: $legacyRoot"
}
if (-not (Test-Path -LiteralPath $migrationHelper -PathType Leaf)) {
    throw "Runtime path migration helper is missing."
}
if (-not (Test-Path -LiteralPath $canonicalPython -PathType Leaf)) {
    throw "Canonical .venv is missing; recreate it before quarantining the legacy runtime."
}
if (-not (Test-Path -LiteralPath $canonicalNodeModules -PathType Container)) {
    throw "Canonical frontend/node_modules is missing; run npm ci before migration."
}

$status = Get-MaintenanceStatus
if (-not [bool]$status.safe_to_restart) {
    $reasons = @($status.reasons) -join "; "
    throw "Layout migration is blocked because the system is busy: $reasons"
}

if (-not $Apply) {
    Write-Host "Nexus layout migration preflight passed." -ForegroundColor Green
    Write-Host "No files were changed. Stop the core services, then rerun with -Apply."
    exit 0
}

Assert-CoreServicesStopped
if ((Test-Path -LiteralPath $legacyData -PathType Container) -and (Test-Path -LiteralPath $canonicalData)) {
    throw "Canonical data directory already exists; refusing to merge runtime data."
}
if (
    -not (Test-Path -LiteralPath $legacyData -PathType Container) -and
    -not (Test-Path -LiteralPath (Join-Path $canonicalData ".nexus-layout-v1.json") -PathType Leaf)
) {
    throw "Neither legacy data nor a completed canonical migration is available."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$quarantineRoot = Join-Path $repoRoot ".nexus-quarantine\layout-$timestamp"
$resolvedQuarantineRoot = Assert-PathWithin -Path $quarantineRoot -Root $repoRoot
New-Item -ItemType Directory -Path $resolvedQuarantineRoot -Force | Out-Null
$manifestItems = [System.Collections.Generic.List[object]]::new()
$dataMoved = $false
$runtimeMigrated = $false

try {
    if (Test-Path -LiteralPath $legacyData -PathType Container) {
        Move-Item -LiteralPath $legacyData -Destination $canonicalData
        $dataMoved = $true
    }
    else {
        Write-Host "Resuming quarantine after a verified runtime migration." -ForegroundColor Yellow
        $runtimeMigrated = $true
    }

    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $repoRoot "src"
        & $canonicalPython $migrationHelper `
            --old-root $legacyRoot `
            --new-root $repoRoot `
            --data-root $canonicalData `
            --apply
        if ($LASTEXITCODE -ne 0) {
            throw "Runtime path migration failed."
        }
        $runtimeMigrated = $true
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
    }

    foreach ($candidate in @(
        (Join-Path $legacyRoot "autoyt.db"),
        (Join-Path $canonicalData "auto_yt.db"),
        (Join-Path $canonicalData "autologin.db"),
        (Join-Path $canonicalData "autoyt.db")
        (Join-Path $canonicalData "database.sqlite")
    )) {
        if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and (Get-Item -LiteralPath $candidate).Length -eq 0) {
            Move-ToQuarantine -Source $candidate -QuarantineRoot $resolvedQuarantineRoot -Manifest $manifestItems
        }
    }

    foreach ($candidate in @(
        (Join-Path $legacyRoot ".venv"),
        (Join-Path $legacyRoot "frontend\node_modules"),
        (Join-Path $legacyRoot ".pytest_cache"),
        (Join-Path $legacyRoot "__pycache__"),
        (Join-Path $legacyRoot "frontend\dist"),
        (Join-Path $legacyRoot "frontend\.codex-build-radar"),
        (Join-Path $repoRoot "Tool-auto-login-GPT-mac-transfer-20260703-015825.zip")
    )) {
        Move-ToQuarantine -Source $candidate -QuarantineRoot $resolvedQuarantineRoot -Manifest $manifestItems
    }

    $legacyScratch = Join-Path $legacyRoot "scratch"
    if (Test-Path -LiteralPath $legacyScratch -PathType Container) {
        Get-ChildItem -LiteralPath $legacyScratch -Directory -Filter "frontend-*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                Move-ToQuarantine -Source $_.FullName -QuarantineRoot $resolvedQuarantineRoot -Manifest $manifestItems
            }
    }

    $manifest = [ordered]@{
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
        delete_after = [DateTimeOffset]::UtcNow.AddDays(7).ToString("o")
        requires_verified_start_stop_restart = $true
        items = @($manifestItems)
    }
    $manifestPath = Join-Path $resolvedQuarantineRoot "manifest.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "Nexus layout migration completed. Quarantine: $resolvedQuarantineRoot" -ForegroundColor Green
}
catch {
    if ($dataMoved -and -not $runtimeMigrated -and (Test-Path -LiteralPath $canonicalData) -and -not (Test-Path -LiteralPath $legacyData)) {
        Move-Item -LiteralPath $canonicalData -Destination $legacyData
    }
    throw
}
