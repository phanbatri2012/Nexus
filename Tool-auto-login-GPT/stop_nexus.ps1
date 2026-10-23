[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 25
)

& (Join-Path $PSScriptRoot "..\stop_nexus.ps1") @PSBoundParameters
exit $LASTEXITCODE
