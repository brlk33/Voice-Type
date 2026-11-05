@echo off
cd /d "%~dp0"
title VoiceType build
set "PYTHONIOENCODING=utf-8"
"%~dp0.venv\Scripts\python.exe" "%~dp0app\make_exe.py"
if errorlevel 1 (
  echo.
  echo BUILD FAILED. Send a screenshot of this window to Claude.
  pause
  exit /b 1
)
timeout /t 4 >nul
