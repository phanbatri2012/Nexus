@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Nexus Studio Engine
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_nexus.ps1" %*
set "NEXUS_EXIT_CODE=%ERRORLEVEL%"

if not "%NEXUS_EXIT_CODE%"=="0" (
    echo.
    echo Nexus Studio Engine failed to start. Review the error above and data\logs.
    if not defined NEXUS_NO_PAUSE if not defined AUTOYT_NO_PAUSE pause
)

exit /b %NEXUS_EXIT_CODE%
