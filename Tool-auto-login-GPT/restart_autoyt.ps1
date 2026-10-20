[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 300)]
    [int]$ReadyTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lifecycle_admin.ps1")

if (-not (Test-AutoYTAdministrator)) {
    $forwardArguments = @()
    if ($NoBrowser) {
        $forwardArguments += "-NoBrowser"
    }
    if ($PSBoundParameters.ContainsKey("ReadyTimeoutSeconds")) {
        $forwardArguments += @("-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds)
    }
    exit (Invoke-AutoYTElevatedScript -ScriptPath $PSCommandPath -Arguments $forwardArguments)
}

$powershellPath = Join-Path $PSHOME "powershell.exe"
$stopScript = Join-Path $PSScriptRoot "stop_autoyt.ps1"
$startScript = Join-Path $PSScriptRoot "start_autoyt.ps1"
$previousNoPause = $env:AUTOYT_NO_PAUSE
$previousRequireGpmIdle = $env:AUTOYT_REQUIRE_GPM_IDLE
$previousRequireIdle = $env:AUTOYT_REQUIRE_IDLE

try {
    $env:AUTOYT_NO_PAUSE = "1"
    $env:AUTOYT_REQUIRE_GPM_IDLE = "1"
    $env:AUTOYT_REQUIRE_IDLE = "1"

    Write-Host "[Auto_YT] Stopping the current system..."
    & $powershellPath -NoLogo -NoProfile -ExecutionPolicy Bypass -File $stopScript
    $stopExitCode = $LASTEXITCODE
    if ($stopExitCode -ne 0) {
        Write-Host ""
        Write-Host "Auto_YT restart aborted because the current system could not be stopped safely." -ForegroundColor Red
        exit $stopExitCode
    }

    Write-Host ""
    Write-Host "[Auto_YT] Starting the system again..."
    $startArguments = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript)
    if ($NoBrowser) {
        $startArguments += "-NoBrowser"
    }
    if ($PSBoundParameters.ContainsKey("ReadyTimeoutSeconds")) {
        $startArguments += @("-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds)
    }
    & $powershellPath @startArguments
    $startExitCode = $LASTEXITCODE
    if ($startExitCode -ne 0) {
        Write-Host ""
        Write-Host "Auto_YT stopped successfully but failed to start again. Review data\logs." -ForegroundColor Red
        exit $startExitCode
    }

    Write-Host ""
    Write-Host "Auto_YT restarted successfully." -ForegroundColor Green
    exit 0
}
finally {
    $env:AUTOYT_NO_PAUSE = $previousNoPause
    $env:AUTOYT_REQUIRE_GPM_IDLE = $previousRequireGpmIdle
    $env:AUTOYT_REQUIRE_IDLE = $previousRequireIdle
}
