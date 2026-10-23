[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 300)]
    [int]$ReadyTimeoutSeconds = 60
)

Write-Warning "Tool-auto-login-GPT\restart_autoyt.ps1 is deprecated; use root restart_nexus.ps1."
& (Join-Path $PSScriptRoot "..\restart_nexus.ps1") @PSBoundParameters
exit $LASTEXITCODE
