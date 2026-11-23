@echo off
setlocal
cd /d "%~dp0"
echo =============================================
echo  PULSE DIAGNOSTICS - WINDOWS 11 DEBUG BUILD
echo =============================================
echo.
echo Uses folder distribution + console + no forced admin elevation.
echo For diagnosis only; admin actions in Optimizer need elevation.
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 build_windows.py --onedir --console --skip-ui --no-admin
) else (
  python build_windows.py --onedir --console --skip-ui --no-admin
)
if errorlevel 1 (
  echo DEBUG BUILD FAILED
  pause
  exit /b 1
)
echo.
echo Run dist\PulseDiagnostics\PulseDiagnostics.exe on the Windows 11 PC.
echo Share %%LOCALAPPDATA%%\PulseDiagnostics\startup.log if it still fails.
pause
