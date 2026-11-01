@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 1) 打包版优先：目录里若有 SecondBrain.exe（或精简版 SecondBrain-Core.exe）
rem    就直接开它，不依赖本机 Python。
set EXE=
if exist "%~dp0SecondBrain.exe" set EXE=%~dp0SecondBrain.exe
if not defined EXE if exist "%~dp0SecondBrain-Core.exe" set EXE=%~dp0SecondBrain-Core.exe
if defined EXE (
  start "" "%EXE%"
  exit /b 0
)

rem 2) 源码版：必须确认「这个解释器里真的装了 pywebview」再启动。
rem    原实现只判断 pythonw 在不在 PATH，命中的解释器没装 pywebview 时，
rem    窗口不会出现、pythonw 又没有控制台，用户看到的就是「双击没反应」。
where python >nul 2>nul
if not %errorlevel%==0 (
  echo [!] 未找到 Python，也没有 SecondBrain.exe。
  echo     请安装 Python 3.12，或使用打包好的 SecondBrain.exe。
  pause
  exit /b 1
)

python -c "import webview" >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0app_window.py"
  exit /b 0
)

rem 3) 有 Python 但没有 pywebview：退回浏览器模式（这个分支会留着控制台，能看到日志）
title Second Brain - keep this window open while using
echo [!] 未安装 pywebview，改用浏览器界面。
echo     想要独立窗口请执行：pip install -r requirements.txt
echo.
start "brain-server" /min cmd /c "python brain.py serve"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8765
echo 浏览器已打开。关闭本窗口即停止服务。
pause
