@echo off
title Disable Auto-Start
echo ============================================
echo  Steam Price Tracker - Disable Auto-Start
echo ============================================
echo.

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

if exist "%STARTUP%\SteamPriceTracker.lnk" (
    del "%STARTUP%\SteamPriceTracker.lnk"
    echo  Auto-start disabled. The server will no longer start at login.
) else (
    echo  Auto-start was not enabled. Nothing to do.
)

echo.
echo  Note: this does not stop a server that is currently running.
echo  To stop it now, open Task Manager and end the "python" process,
echo  or just restart your PC.
echo.
pause
