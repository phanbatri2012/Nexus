@echo off
setlocal EnableExtensions
>&2 echo [DEPRECATED] run_autoyt_restart.bat is deprecated; use run_nexus_restart.bat.
call "%~dp0run_nexus_restart.bat" %*
exit /b %ERRORLEVEL%
