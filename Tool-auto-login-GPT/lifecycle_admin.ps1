function Test-AutoYTAdministrator {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Invoke-AutoYTElevatedScript {
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
        ('"' + $resolvedScript + '"')
    ) + $Arguments

    try {
        Write-Host "[Auto_YT] Requesting Administrator permission..." -ForegroundColor Yellow
        $process = Start-Process `
            -FilePath $powershellPath `
            -ArgumentList $processArguments `
            -Verb RunAs `
            -Wait `
            -PassThru
        return [int]$process.ExitCode
    }
    catch {
        Write-Host "Auto_YT could not obtain Administrator permission: $($_.Exception.Message)" -ForegroundColor Red
        return 1223
    }
}
