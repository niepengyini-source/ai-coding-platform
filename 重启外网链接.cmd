@echo off
chcp 65001 >nul
title AI辅助编码平台 · 重启外网链接
cd /d "%~dp0"
echo.
echo 	作用：保持平台不动，只重建外网链接（本机和局域网地址暂时不受影响）。
echo 	需要：电脑开机、联网、不休眠。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\外网脚本.ps1" -Mode Restart