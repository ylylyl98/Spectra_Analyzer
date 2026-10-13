@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [Error] Local Python not found: .venv\Scripts\pythonw.exe
    pause
    exit /b 1
)

if not exist "run_qt.py" (
    echo [Error] App script not found: run_qt.py
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "run_qt.py"
exit /b 0
