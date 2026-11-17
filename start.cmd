@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Lab check-in - do not close this window

rem ===== 实验室统一邮箱默认配置 =====
rem 默认使用项目当前配置的 QQ 邮箱；mail.env 中的值会在 PowerShell 启动阶段覆盖这些默认值。
rem 授权码不要直接写在本文件中，请通过 scripts\configure-mail.ps1 配置到 mail.env。
if not defined LAB_SMTP_HOST set "LAB_SMTP_HOST=smtp.qq.com"
if not defined LAB_SMTP_PORT set "LAB_SMTP_PORT=465"
if not defined LAB_SMTP_SECURITY set "LAB_SMTP_SECURITY=ssl"
if not defined LAB_SMTP_USER set "LAB_SMTP_USER=2230274247@qq.com"
if not defined LAB_SMTP_FROM set "LAB_SMTP_FROM=2230274247@qq.com"

powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0scripts\start-device.ps1"
echo.
pause
