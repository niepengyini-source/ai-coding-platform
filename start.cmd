@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "DJANGO_SETTINGS_MODULE=config.settings"
if not exist ".venv\Scripts\python.exe" (
  echo 请先按README安装Python虚拟环境和依赖。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 manage.py migrate --noinput
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 manage.py collectstatic --noinput
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 manage.py initialize
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 server.py %*
:failed
pause
