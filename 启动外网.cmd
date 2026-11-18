@echo off
chcp 65001 >nul
title AI辅助编码平台 · 启动外网访问
cd /d "%~dp0"
echo.
echo 	正在启动平台并准备外网链接，请稍候……
echo 	正常情况下会沿用现有链接；只有链接失效时才换新地址。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\外网脚本.ps1" -Mode Start
echo.
echo 	如果窗口显示“按任意键继续”且没有退出，那是平台的运行窗口。
echo 	双击弹窗出现后不要关闭它；关闭它等同于关闭平台（外网也会断）。
echo 	更安全的暂停方式：双击 stop.cmd，输入 STOP。
echo.
pause