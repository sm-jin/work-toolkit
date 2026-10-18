@echo off
rem Self-test: screen capture, displays, lock state, OCR. Sends no key presses or clicks.
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python -m app.check
pause
