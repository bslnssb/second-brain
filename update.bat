@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

if not exist scope.json (
  echo [!] 未找到 scope.json。
  echo     请先把 scope.example.json 复制为 scope.json，并改好 include_dirs。
  pause
  exit /b 1
)

rem 打包版优先：exe 里已自带扫描器、索引器和 OCR，不需要本机 Python
rem update 一条命令走完三件事：重扫清单 → 索引新增/变更 → 清理失效条目
if exist "%~dp0SecondBrain.exe" (
  "%~dp0SecondBrain.exe" update
  echo.
  echo 全部完成，可以关闭本窗口。
  pause
  exit /b 0
)

where python >nul 2>nul
if not %errorlevel%==0 (
  echo [!] 未找到 Python，也没有 SecondBrain.exe。
  echo     更新需要 Python 3.12（只做搜索则不需要）。
  pause
  exit /b 1
)

echo [1/3] 扫描磁盘上的文档...
python scan_docs.py
echo.
echo [2/3] 索引新增与变更的文件...
python brain.py index scope.json
echo.
echo [3/3] 清理已删除文件的索引...
python brain.py prune
echo.
echo 全部完成，可以关闭本窗口。
pause
