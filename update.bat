@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "UV=%ROOT%tools\uv\uv.exe"
set "UV_CACHE_DIR=%ROOT%cache\uv"
set "UV_PYTHON_INSTALL_DIR=%ROOT%tools\python"
set "PY=%ROOT%.venv\Scripts\python.exe"
title VoiceType update

echo === Stopping running VoiceType ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*VoiceType*app.py*' -or $_.ExecutablePath -like '*VoiceType\tools\llama*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo === Installing new libraries ===
"%UV%" pip install --python "%PY%" -r requirements.txt -c constraints.txt --upgrade-package yt-dlp --upgrade-package youtube-transcript-api || goto :fail

echo === Starting VoiceType ===
if exist "%ROOT%.venv\Scripts\VoiceType.exe" (start "" "%ROOT%.venv\Scripts\VoiceType.exe" "%ROOT%app\app.py") else (start "" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%app\app.py")
echo DONE
timeout /t 3 >nul
exit /b 0

:fail
echo UPDATE FAILED. Send a screenshot of this window to Claude.
pause
exit /b 1
