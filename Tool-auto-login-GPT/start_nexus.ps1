[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 300)]
    [int]$ReadyTimeoutSeconds = 60
)

& (Join-Path $PSScriptRoot "..\start_nexus.ps1") @PSBoundParameters
exit $LASTEXITCODE
