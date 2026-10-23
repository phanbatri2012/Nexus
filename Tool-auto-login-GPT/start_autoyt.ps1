[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 300)]
    [int]$ReadyTimeoutSeconds = 60
)

Write-Warning "Tool-auto-login-GPT\start_autoyt.ps1 is deprecated; use root start_nexus.ps1."
& (Join-Path $PSScriptRoot "..\start_nexus.ps1") @PSBoundParameters
exit $LASTEXITCODE
