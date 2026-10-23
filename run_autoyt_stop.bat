@echo off
setlocal EnableExtensions
>&2 echo [DEPRECATED] run_autoyt_stop.bat is deprecated; use run_nexus_stop.bat.
call "%~dp0run_nexus_stop.bat" %*
exit /b %ERRORLEVEL%
