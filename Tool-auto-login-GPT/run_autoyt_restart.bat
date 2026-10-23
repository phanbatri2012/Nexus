@echo off
>&2 echo [DEPRECATED] Tool-auto-login-GPT\run_autoyt_restart.bat forwards to root run_nexus_restart.bat.
call "%~dp0..\run_nexus_restart.bat" %*
exit /b %ERRORLEVEL%
