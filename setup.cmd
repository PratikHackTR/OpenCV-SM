@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.12 -m venv .venv
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt --retries 8 --resume-retries 15 --timeout 60
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m webmotion.models
if errorlevel 1 goto fail
echo Setup complete. Run start.cmd to open WebMotion.
pause
exit /b 0
:fail
echo Setup failed. Python 3.12 x64 and an internet connection are required.
pause
exit /b 1
