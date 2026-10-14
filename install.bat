@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo --- Screenshot QA: installing dependencies ---
python -m venv .venv
if errorlevel 1 (
    echo.
    echo [ERROR] python not found. Please install Python 3.9+ and check "Add Python to PATH".
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] dependency install failed. Check network and retry.
    pause
    exit /b 1
)
echo.
echo Done. Double-click run.bat to start.
pause