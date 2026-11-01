@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Open the standalone desktop window (pywebview). Falls back to the browser
rem when pywebview is not installed, so it never fails silently.
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0app_window.py"
  exit /b 0
)
where python >nul 2>nul
if %errorlevel%==0 (
  title Second Brain - keep this window open while using
  echo pywebview not available, falling back to browser mode...
  start "brain-server" /min cmd /c "python brain.py serve"
  timeout /t 2 /nobreak >nul
  start "" http://127.0.0.1:8765
  echo The browser should open now. You can close this window.
  exit /b 0
)
echo [!] Python not found in PATH.
echo     Please install Python 3.12 and re-run this file.
pause
