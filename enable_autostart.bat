@echo off
title Enable Auto-Start
echo ============================================
echo  Steam Price Tracker - Enable Auto-Start
echo ============================================
echo.

:: Make sure dependencies are installed before enabling silent auto-start
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found. Install it and run start.bat once first.
    pause & exit /b 1
)
echo  Checking dependencies...
python -m pip install -r "%~dp0backend\requirements.txt" -q

:: Create a shortcut to the silent launcher in the user's Startup folder
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%~dp0run_silent.vbs"

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\SteamPriceTracker.lnk');" ^
  "$s.TargetPath='%VBS%';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.Description='Steam Price Tracker server';" ^
  "$s.Save()"

if %errorlevel% neq 0 (
    echo  ERROR: could not create the startup shortcut.
    pause & exit /b 1
)

echo.
echo  Done. The server will now start silently every time you log in.
echo  Starting it now too...
start "" "%~dp0run_silent.vbs"
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8770
echo.
echo  Open it any time at:  http://127.0.0.1:8770
echo  From your phone/tablet on the same WiFi, use this PC's IP (see instructions).
echo.
pause
