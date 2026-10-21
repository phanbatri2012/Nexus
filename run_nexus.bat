@echo off
setlocal EnableExtensions
cd /d "%~dp0Tool-auto-login-GPT"
call run_nexus.bat %*
exit /b %ERRORLEVEL%
