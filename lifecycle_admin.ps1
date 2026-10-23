function Test-NexusAdministrator {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Invoke-NexusElevatedScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    $resolvedScript = [System.IO.Path]::GetFullPath($ScriptPath)
    $powershellPath = Join-Path $PSHOME "powershell.exe"
    $processArguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $resolvedScript
    ) + $Arguments

    try {
        Write-Host "[Nexus Studio Engine] Requesting Administrator permission..." -ForegroundColor Yellow
        $process = Start-Process `
            -FilePath $powershellPath `
            -ArgumentList $processArguments `
            -Verb RunAs `
            -PassThru
        # Start-Process -Wait follows the entire elevated process tree. Nexus
        # services are intentionally long-lived children, so wait only for the
        # elevated lifecycle script itself before propagating its exit code.
        $process.WaitForExit()
        return [int]$process.ExitCode
    }
    catch {
        Write-Host "Nexus Studio Engine could not obtain Administrator permission: $($_.Exception.Message)" -ForegroundColor Red
        return 1223
    }
}
