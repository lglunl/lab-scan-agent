@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Lab check-in - do not close this window
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-device.ps1"
echo.
pause
