@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python server.py serve --port 8770 --pages sample-pages --open
) else (
  py -3 server.py serve --port 8770 --pages sample-pages --open
)
echo.
echo 服务已停止，按任意键关闭窗口。
pause >nul
