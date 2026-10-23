@echo off
call "%~dp0..\run_nexus_restart.bat" %*
exit /b %ERRORLEVEL%
