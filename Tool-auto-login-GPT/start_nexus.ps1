<#
.SYNOPSIS
    Nexus Studio Engine Canonical Startup Wrapper
#>
[CmdletBinding()]
param()

$target = Join-Path $PSScriptRoot "start_autoyt.ps1"
& $target @args
exit $LASTEXITCODE
