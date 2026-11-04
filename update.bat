@echo off
rem 编码说明：本文件保存为 GBK(ANSI)、CRLF 行尾，不要改成 UTF-8——cmd 解析含中文的批处理时会错位。
cd /d "%~dp0"
set "PYTHONIOENCODING=gbk:replace"
set PYTHONUNBUFFERED=1

if not exist "%~dp0scope.json" (
  echo [!] 未找到 scope.json，请先把 scope.example.json 复制一份改名为 scope.json，改好里面的文件夹。
  pause
  exit /b 1
)

rem 有源码就优先跑源码；只有发布包（没有 .py）时才走 exe。
set "SB_CMD="
if exist "%~dp0brain.py" (
  where python >nul 2>nul
  if not errorlevel 1 set "SB_CMD=python brain.py update"
)
if not defined SB_CMD if exist "%~dp0SecondBrain.exe" set "SB_CMD=SecondBrain.exe update"
if not defined SB_CMD if exist "%~dp0SecondBrain-Core.exe" set "SB_CMD=SecondBrain-Core.exe update"
if not defined SB_CMD (
  echo [!] 没找到可运行的程序：目录里既没有 brain.py + Python，也没有 SecondBrain.exe。
  pause
  exit /b 1
)

echo 开始更新：扫描磁盘 -> 索引新增/变更文件 -> 清理已删除条目
echo 期间可以最小化本窗口，但不要关闭；扫描阶段可能几十秒不输出，属于正常。
echo.

%SB_CMD%
set "SB_RC=%errorlevel%"
echo.
if not "%SB_RC%"=="0" (
  echo [!] 这次更新没有成功完成（退出码 %SB_RC%）。上面带 [!] 的那行是原因，先别关窗口。
  echo     常见原因：要索引的文件夹被搬走或改名了；或者另一个更新还在跑。
  echo     索引本身没有被清空，按提示处理后再双击一次即可。
  pause
  exit /b %SB_RC%
)
echo 更新完成，可以关闭本窗口。
pause
