@echo off
setlocal EnableExtensions
>&2 echo [DEPRECATED] run_autoyt.bat is deprecated; use run_nexus.bat.
call "%~dp0run_nexus.bat" %*
exit /b %ERRORLEVEL%
