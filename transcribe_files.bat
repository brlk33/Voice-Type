@echo off
rem Drag audio/video files onto this file, or double-click to choose them.
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
"%~dp0.venv\Scripts\python.exe" "%~dp0app\transcribe_cli.py" %*
echo.
pause
