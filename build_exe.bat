@echo off
rem 编码说明：GBK(ANSI) + CRLF，不要改成 UTF-8。
setlocal
cd /d "%~dp0"

echo [1/2] 打包完整版（含 OCR / PDF / Office 解析）...
python -m PyInstaller --noconfirm --clean second-brain-app.spec || goto :fail

echo [2/2] 打包精简版（只要搜索，体积小）...
python -m PyInstaller --noconfirm --clean second-brain-core.spec || goto :fail

echo.
echo 打包完成，产物在 dist 目录。
echo 注意：dist 里只放 exe 和 _internal，别把 brain.db / scope.json 拷进去。
pause
exit /b 0

:fail
echo.
echo [!] 打包失败，看上面的报错。
pause
exit /b 2
