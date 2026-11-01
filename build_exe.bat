@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if not %errorlevel%==0 (
  echo [!] Python not found in PATH. Install Python 3.12 first.
  pause
  exit /b 1
)
echo Building full edition (with OCR) ...
python -m PyInstaller --noconfirm --distpath "..\build" --workpath "..\build\_work" second-brain.spec
if not %errorlevel%==0 (
  echo [!] Build failed. See the log above.
  pause
  exit /b 1
)
echo.
echo Done: ..\build\second-brain.exe
echo For the core edition (no OCR), comment out the rapidocr_onnxruntime
echo and pymupdf collect_all blocks in second-brain.spec and build again.
pause
