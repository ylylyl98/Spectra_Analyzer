@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto VENV_READY

echo [Setup] Creating virtual environment...
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 (
    echo [Error] Failed to create .venv. Install Python 3 and try again.
    pause
    exit /b 1
)

:VENV_READY
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [Error] Failed to activate virtual environment.
    pause
    exit /b 1
)

if not exist ".venv\.deps_installed" (
    echo [Setup] Installing requirements...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [Error] Dependency installation failed.
        pause
        exit /b 1
    )
    type nul > ".venv\.deps_installed"
)

echo [Run] Starting Spectra Analysis app...
python run_qt.py
if errorlevel 1 (
    echo [Error] App exited with an error.
    pause
    exit /b 1
)

endlocal
