@echo off
setlocal EnableExtensions
rem Bumps APP_VERSION decimal (1.3.9 -> 1.4.0) then packs exe.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] Missing %PY%
    echo Create .venv and install pyinstaller first.
    pause
    exit /b 1
)
"%PY%" scripts\build_windows.py exe
if errorlevel 1 (
    echo [ERROR] build failed
    pause
    exit /b 1
)
pause
