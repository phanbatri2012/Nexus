<#
.SYNOPSIS
    Nexus Studio Engine Canonical Restart Wrapper
#>
[CmdletBinding()]
param()

$target = Join-Path $PSScriptRoot "restart_autoyt.ps1"
& $target @args
exit $LASTEXITCODE
