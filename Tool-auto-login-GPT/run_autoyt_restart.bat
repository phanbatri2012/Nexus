@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Restart Auto_YT
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_autoyt.ps1" %*
set "AUTOYT_EXIT_CODE=%ERRORLEVEL%"

if not "%AUTOYT_EXIT_CODE%"=="0" (
    echo Auto_YT restart failed. Review the error above and data\logs.
    if not defined AUTOYT_RESTART_NO_PAUSE pause
)

exit /b %AUTOYT_EXIT_CODE%
