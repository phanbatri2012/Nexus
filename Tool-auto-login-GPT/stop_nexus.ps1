<#
.SYNOPSIS
    Nexus Studio Engine Canonical Stop Wrapper
#>
[CmdletBinding()]
param()

$target = Join-Path $PSScriptRoot "stop_autoyt.ps1"
& $target @args
exit $LASTEXITCODE
