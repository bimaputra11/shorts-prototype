@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\shorts-tunnel.ps1" %*
if errorlevel 1 (
    echo.
    echo Tunnel controller exited with an error.
    pause
)
endlocal
