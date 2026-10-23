@echo off
call "%~dp0..\run_nexus_stop.bat" %*
exit /b %ERRORLEVEL%
