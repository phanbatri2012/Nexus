[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 25
)

Write-Warning "Tool-auto-login-GPT\stop_autoyt.ps1 is deprecated; use root stop_nexus.ps1."
& (Join-Path $PSScriptRoot "..\stop_nexus.ps1") @PSBoundParameters
exit $LASTEXITCODE
