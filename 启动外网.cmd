@echo off
chcp 65001 >nul
title AI辅助编码平台 · 启动外网访问
cd /d "%~dp0"
echo.
echo    正在启动平台并检查外网链接，请稍候（首次可能要 1-2 分钟）……
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\外网脚本.ps1" -Mode Start -NoPause
if errorlevel 1 goto failed
echo.
echo    外网地址见上方高亮那一行，也已写入 runtime\外网访问地址.txt
echo.
echo    按任意键关闭本窗口。平台和隧道会继续在后台运行。
pause >nul
exit /b 0

:failed
echo.
echo    启动没有完成：原因见上方提示。
echo    平台数据没有改动；本机平台通常仍可访问 http://127.0.0.1:8000/login/
echo    排查方法：双击“启动外网.cmd”再看一次提示，或查看 runtime 目录下最新的 platform-*.err.log
echo.
echo    按任意键关闭本窗口。
pause >nul
exit /b 1
