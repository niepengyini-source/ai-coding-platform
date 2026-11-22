@echo off
chcp 65001 >nul
title AI辅助编码平台 · 重启外网链接
cd /d "%~dp0"
echo.
echo    正在更换外网链接，平台会继续运行，请稍候……
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\外网脚本.ps1" -Mode Restart -NoPause
if errorlevel 1 goto failed
echo.
echo    按任意键关闭本窗口。平台和隧道会继续在后台运行。
pause >nul
exit /b 0

:failed
echo.
echo    更换没有完成：原因见上方提示。
echo    按任意键关闭本窗口。
pause >nul
exit /b 1
