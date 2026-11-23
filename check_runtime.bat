@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "from PySide6.QtWebEngineWidgets import QWebEngineView; from PySide6.QtWebChannel import QWebChannel; print('Qt Chromium / WebChannel installed')"
) else (
  python -c "from PySide6.QtWebEngineWidgets import QWebEngineView; from PySide6.QtWebChannel import QWebChannel; print('Qt Chromium / WebChannel installed')"
)
echo.
pause
