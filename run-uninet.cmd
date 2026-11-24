@echo off
setlocal
cd /d "%~dp0"
title Cuevio
chcp 65001 >nul
set "PYTHONUTF8=1"
set "UNINET_PYTHON=.venv\Scripts\python.exe"

if not exist "%UNINET_PYTHON%" (
    echo Cuevio - preparing Python environment...
    python -m venv .venv
    if errorlevel 1 goto failed
)
"%UNINET_PYTHON%" -c "import playwright, keyring, rich, prompt_toolkit" >nul 2>&1
if errorlevel 1 (
    echo Cuevio - installing dependencies...
    "%UNINET_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)

"%UNINET_PYTHON%" uninet.py %*
exit /b %errorlevel%

:failed
pause
exit /b 1
