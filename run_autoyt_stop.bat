@echo off
setlocal EnableExtensions
cd /d "%~dp0Tool-auto-login-GPT"
call run_autoyt_stop.bat %*
exit /b %ERRORLEVEL%
