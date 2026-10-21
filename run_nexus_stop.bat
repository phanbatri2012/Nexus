@echo off
setlocal EnableExtensions
cd /d "%~dp0Tool-auto-login-GPT"
call run_nexus_stop.bat %*
exit /b %ERRORLEVEL%
