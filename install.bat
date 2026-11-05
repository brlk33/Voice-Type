@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "UV=%ROOT%tools\uv\uv.exe"
set "UV_CACHE_DIR=%ROOT%cache\uv"
set "UV_PYTHON_INSTALL_DIR=%ROOT%tools\python"
set "UV_HTTP_TIMEOUT=300"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "SILENT="
set "NOSTART="
rem /silent: run from VoiceType-Setup.exe - no pauses, the setup window shows the output
if /i "%~1"=="/silent" (
  set "SILENT=1"
  set "NOSTART=--no-start"
  rem the setup window reads output in the Windows ANSI code page
  set "PYTHONIOENCODING=mbcs:replace"
  set "PYTHONUTF8=0"
  set "NO_COLOR=1"
)
title VoiceType install

echo.
echo === [1/6] Downloading uv (Python manager) ===
if not exist "%UV%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $z=Join-Path $env:TEMP 'voicetype-uv.zip'; Invoke-WebRequest -UseBasicParsing 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $z; Expand-Archive -Force $z 'tools\uv'"
)
if not exist "%UV%" goto :fail

echo.
echo === [2/6] Python 3.11 ===
if not exist "%PY%" (
  "%UV%" venv --python 3.11 --python-preference only-managed .venv || goto :fail
)

echo.
echo === [3/6] PyTorch with CUDA (about 3 GB, be patient) ===
"%UV%" pip install --python "%PY%" torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu126 || goto :fail

echo.
echo === [4/6] Libraries ===
"%UV%" pip install --python "%PY%" -r requirements.txt -c constraints.txt || goto :fail

echo.
echo === [5/6] Model download and check ===
"%PY%" app\setup_check.py || goto :fail

echo.
echo === [6/6] VoiceType.exe and shortcuts ===
"%PY%" app\make_exe.py %NOSTART% || goto :fail

echo.
echo ==========================================================
echo  DONE. Start VoiceType with the desktop shortcut or start.bat
echo ==========================================================
if not defined SILENT pause
exit /b 0

:fail
echo.
echo ##########################################################
echo  INSTALL FAILED. Open an issue on GitHub with a screenshot of this window.
echo  Log: %ROOT%logs\install.log
echo ##########################################################
if not defined SILENT pause
exit /b 1
