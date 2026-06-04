@echo off
title Steam Price Tracker Server
echo ============================================
echo  Steam Price Tracker
echo ============================================
echo.
echo  KEEP THIS WINDOW OPEN (minimise it to the taskbar).
echo  Closing this window will stop the server.
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found.
    echo  Install Python 3.11+ from https://python.org
    echo  Tick "Add Python to PATH" during install.
    pause & exit /b 1
)

cd /d "%~dp0backend"

echo  Checking dependencies...
python -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo  ERROR: dependency install failed.
    pause & exit /b 1
)

echo  Server starting on http://127.0.0.1:8770
echo  (also reachable from other devices on your WiFi)
echo  Opening browser in 3 seconds...
echo.

:: Open browser after 3 s (server needs a moment to be ready)
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8770"

:: 0.0.0.0 = reachable from phones/tablets on the same WiFi via this PC's IP
python -m uvicorn main:app --host 0.0.0.0 --port 8770
