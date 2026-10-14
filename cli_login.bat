@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python.exe"
"%PY%" "tools\cli_login.py"
echo.
pause