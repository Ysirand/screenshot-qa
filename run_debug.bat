@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python.exe"
echo Debug mode: logs are printed below. Close this window to quit.
echo.
"%PY%" "main.py"
echo.
pause