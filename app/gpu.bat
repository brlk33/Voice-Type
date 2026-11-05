@echo off
setlocal
rem lives in app\ so that updates always deliver it; works from the program folder
cd /d "%~dp0.."
set "ROOT=%CD%\"
set "UV=%ROOT%tools\uv\uv.exe"
set "UV_CACHE_DIR=%ROOT%cache\uv"
set "UV_PYTHON_INSTALL_DIR=%ROOT%tools\python"
set "UV_HTTP_TIMEOUT=300"
set "PY=%ROOT%.venv\Scripts\python.exe"
rem gpu.bat gpu  - switch to PyTorch for NVIDIA cards (fast, about 2.5 GB download)
rem gpu.bat cpu  - switch back to the light processor-only PyTorch
title VoiceType: graphics card acceleration

echo === Closing VoiceType ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*VoiceType*app.py*' -or $_.ExecutablePath -like '*VoiceType\tools\llama*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 >nul

if /i "%~1"=="gpu" goto :gpu
echo.
echo === Installing the light PyTorch - about 200 MB ===
"%UV%" pip install --python "%PY%" --reinstall-package torch --reinstall-package torchaudio torch==2.7.1+cpu torchaudio==2.7.1+cpu --index-url https://download.pytorch.org/whl/cpu || goto :fail
goto :done

:gpu
echo.
echo === Downloading PyTorch for NVIDIA graphics cards - about 2.5 GB, please wait ===
"%UV%" pip install --python "%PY%" --reinstall-package torch --reinstall-package torchaudio torch==2.7.1+cu126 torchaudio==2.7.1+cu126 --index-url https://download.pytorch.org/whl/cu126 || goto :fail

:done
echo.
echo === Cleaning up ===
"%PY%" app\slim.py
echo.
echo === Starting VoiceType ===
if exist "%ROOT%.venv\Scripts\VoiceType.exe" (start "" "%ROOT%.venv\Scripts\VoiceType.exe" "%ROOT%app\app.py") else (start "" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%app\app.py")
timeout /t 3 >nul
exit /b 0

:fail
echo.
echo FAILED. VoiceType will start with the previous version of PyTorch if it is still there.
echo Send a screenshot of this window to the developer.
if exist "%ROOT%.venv\Scripts\VoiceType.exe" (start "" "%ROOT%.venv\Scripts\VoiceType.exe" "%ROOT%app\app.py")
pause
exit /b 1
