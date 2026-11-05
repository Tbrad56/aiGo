@echo off
rem Build dist\aiGo\aiGo.exe. Run from anywhere; takes a few minutes the first time.
cd /d "%~dp0"
rem Rebuilding while aiGo is open half-deletes dist\aiGo, so stop here instead.
tasklist /fi "imagename eq aiGo.exe" | find /i "aiGo.exe" >nul && (
  echo aiGo is running. Quit it from the tray icon first, then run this again.
  goto :fail
)
if not exist .build-venv\Scripts\python.exe (
  echo Creating build environment...
  py -3.12 -m venv .build-venv || goto :fail
)
.build-venv\Scripts\python -m pip install -q -c packaging\build-constraints.txt pyinstaller . || goto :fail
.build-venv\Scripts\python -m PyInstaller packaging\aigo.spec --noconfirm --clean --distpath dist --workpath build || goto :fail
echo.
echo Built: %~dp0dist\aiGo\aiGo.exe
pause
exit /b 0
:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
