@echo off
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
"%~dp0.venv\Scripts\python.exe" "%~dp0app\app.py"
pause
