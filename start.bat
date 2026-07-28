@echo off
chcp 65001 >nul 2>&1
title Hermes Profile Manager

cd /d "%~dp0"

echo.
echo   Hermes Profile Manager - Setup
echo   ══════════════════════════════
echo.

REM 检查 venv
if not exist ".venv\Scripts\python.exe" (
    echo   [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo   ERROR: Failed to create venv. Make sure Python 3.11+ is installed.
        pause
        exit /b 1
    )
) else (
    echo   [1/3] Virtual environment found.
)

echo   [2/3] Installing dependencies...
".venv\Scripts\pip.exe" install -q -r requirements.txt 2>nul
if errorlevel 1 (
    echo   Retrying with upgraded pip...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\pip.exe" install -q -r requirements.txt
)

echo   [3/3] Starting server...
echo.
echo   ┌──────────────────────────────────────┐
echo   │  http://127.0.0.1:18520              │
echo   │  Press Ctrl+C to stop                 │
echo   └──────────────────────────────────────┘
echo.

".venv\Scripts\python.exe" app.py
pause
