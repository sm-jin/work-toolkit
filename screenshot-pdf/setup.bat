@echo off
rem One-time setup on Windows: Python virtualenv + packages, Tesseract OCR
cd /d "%~dp0"

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo Python 3 is required: https://www.python.org/downloads/
  echo During install, check "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe %PY% -m venv .venv
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Package install failed.
  pause
  exit /b 1
)

where tesseract >nul 2>&1 && goto ocr_ok
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto ocr_ok
if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" goto ocr_ok
echo Installing Tesseract OCR...
winget install -e --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
if errorlevel 1 echo Install Tesseract manually: https://github.com/UB-Mannheim/tesseract/wiki
:ocr_ok

echo.
echo Setup complete. Run check.bat to test, then run.bat to start the server.
pause
