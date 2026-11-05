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
set "GPU="
rem /silent: run from VoiceType-Setup.exe - no pauses, the setup window shows the output
rem /gpu:    PyTorch for NVIDIA cards (fast, +3.7 GB); without it the light processor-only PyTorch
for %%A in (%*) do (
  if /i "%%~A"=="/silent" set "SILENT=1"
  if /i "%%~A"=="/gpu" set "GPU=1"
)
if defined SILENT (
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
rem an existing install keeps its PyTorch unless /gpu asks for the NVIDIA one
if not defined GPU "%PY%" -c "import torch" >nul 2>&1 && goto :torch_done
if defined GPU goto :torch_gpu
echo === [3/6] PyTorch, light processor version - about 200 MB ===
"%UV%" pip install --python "%PY%" torch==2.7.1+cpu torchaudio==2.7.1+cpu --index-url https://download.pytorch.org/whl/cpu || goto :fail
goto :torch_done
:torch_gpu
echo === [3/6] PyTorch for NVIDIA graphics cards - about 2.5 GB, be patient ===
"%UV%" pip install --python "%PY%" torch==2.7.1+cu126 torchaudio==2.7.1+cu126 --index-url https://download.pytorch.org/whl/cu126 || goto :fail
:torch_done

echo.
echo === [4/6] Libraries ===
"%UV%" pip install --python "%PY%" -r requirements.txt -c constraints.txt || goto :fail

echo.
echo === Cleaning up files that are not needed ===
"%PY%" app\slim.py

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
