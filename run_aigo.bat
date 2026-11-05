@echo off
rem Start aiGo. Extra options pass through, e.g.  run_aigo.bat --fake
cd /d "%~dp0"
py -3.12 -m aigo %*
if errorlevel 1 pause
