@echo off
setlocal EnableExtensions
cd /d "%~dp0Tool-auto-login-GPT"
call run_autoyt_restart.bat %*
exit /b %ERRORLEVEL%
