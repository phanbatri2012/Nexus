@echo off
>&2 echo [DEPRECATED] Tool-auto-login-GPT\run_autoyt.bat forwards to root run_nexus.bat.
call "%~dp0..\run_nexus.bat" %*
exit /b %ERRORLEVEL%
