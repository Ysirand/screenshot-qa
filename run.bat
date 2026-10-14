@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=pythonw.exe"
start "" "%PY%" "main.py"
exit /b 0