@echo off
rem Start the capture server and open the printed URL in a browser.
rem To allow access from a phone on the same Wi-Fi: set SHOTPDF_HOST=0.0.0.0 before the python line.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
.venv\Scripts\python -m app.server
pause
