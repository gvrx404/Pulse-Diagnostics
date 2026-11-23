@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 build_windows.py --skip-ui --no-admin
) else (
  python build_windows.py --skip-ui --no-admin
)
if errorlevel 1 (
  echo.
  echo BUILD FAILED. Review error above.
  pause
  exit /b 1
)
echo.
echo Your EXE is at dist\PulseDiagnostics.exe
pause
