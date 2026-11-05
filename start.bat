@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\VoiceType.exe" (
  start "" "%~dp0.venv\Scripts\VoiceType.exe" "%~dp0app\app.py"
) else (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\app.py"
)
