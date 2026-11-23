@echo off
setlocal
cd /d "%~dp0"
echo Testing scoring and app result interfaces (NOT the hardware sensors)...
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m unittest discover -s tests -v
) else (
  python -m unittest discover -s tests -v
)
if errorlevel 1 (
  echo TESTS FAILED
  pause
  exit /b 1
)
echo TESTS PASSED. Run actual Windows hardware diagnostics in Pulse.
pause
