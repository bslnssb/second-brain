@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist scope.json (
  echo [!] scope.json not found.
  echo     Copy scope.example.json to scope.json first,
  echo     then edit include_dirs to point at your own folders.
  pause
  exit /b 1
)
where python >nul 2>nul
if not %errorlevel%==0 (
  echo [!] Python not found in PATH.
  echo     Updating needs Python 3.12 ^(searching alone does not^).
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

echo [1/3] Rescanning drives for documents (fiction folders and noise always excluded)...
python scan_docs.py
echo.
echo [2/3] Indexing newly found files (already-indexed files are skipped)...
python brain.py index scope.json
echo.
echo [3/3] Removing entries for deleted files...
python brain.py prune
echo.
echo All done. You can close this window.
pause
