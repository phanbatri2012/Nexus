@echo off
>&2 echo [DEPRECATED] Tool-auto-login-GPT\run_autoyt_stop.bat forwards to root run_nexus_stop.bat.
call "%~dp0..\run_nexus_stop.bat" %*
exit /b %ERRORLEVEL%
