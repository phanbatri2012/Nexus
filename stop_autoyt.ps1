[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 25
)

Write-Warning "stop_autoyt.ps1 is deprecated; use stop_nexus.ps1."
& (Join-Path $PSScriptRoot "stop_nexus.ps1") @PSBoundParameters
exit $LASTEXITCODE
