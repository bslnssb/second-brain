@echo off
rem 编码说明：GBK(ANSI) + CRLF，不要改成 UTF-8。
cd /d "%~dp0"

rem 1) 有源码就优先跑源码（开发期代码比根目录的 exe 新）。
set "SB_EXE="
if exist "%~dp0brain.py" (
  where pythonw >nul 2>nul
  if not errorlevel 1 (
    pythonw -c "import webview" >nul 2>nul
    if not errorlevel 1 (
      start "" pythonw "%~dp0app_window.py"
      exit /b 0
    )
  )
)

rem 2) 发布包：优先完整版，其次精简版。
if exist "%~dp0SecondBrain.exe" set "SB_EXE=%~dp0SecondBrain.exe"
if not defined SB_EXE if exist "%~dp0SecondBrain-Core.exe" set "SB_EXE=%~dp0SecondBrain-Core.exe"
if defined SB_EXE (
  start "" "%SB_EXE%"
  exit /b 0
)

echo [!] 没找到可运行的程序：目录里既没有 SecondBrain.exe，也没有 brain.py + Python + pywebview。
echo     请安装 Python 3.12 后执行 pip install -r requirements.txt，或下载打包好的 exe。
pause
exit /b 1
