@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Build the Windows executables.
rem   build_exe.bat           -> full + core
rem   build_exe.bat full      -> full only (with OCR)
rem   build_exe.bat core      -> core only (no OCR)
rem
rem Both editions are onedir: PyInstaller already explodes the bundle at build
rem time, so indexing never pays a per-file unpack cost. See the .spec headers.

set MODE=%~1
if "%MODE%"=="" set MODE=all

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found in PATH. Install Python 3.12 first.
  pause
  exit /b 1
)

if /i "%MODE%"=="full" goto :full
if /i "%MODE%"=="core" goto :core
if /i "%MODE%"=="all"  goto :full
echo Usage: build_exe.bat [full^|core^|all]
exit /b 2

:full
echo Building full edition with OCR ...
python -m PyInstaller --noconfirm --distpath "..\build" --workpath "..\build\_work" second-brain-app.spec
if errorlevel 1 (
  echo [!] Full build failed. See the log above.
  pause
  exit /b 1
)
if /i not "%MODE%"=="all" goto :done

:core
echo Building core edition without OCR ...
python -m PyInstaller --noconfirm --distpath "..\build-core" --workpath "..\build-core\_work" second-brain-core.spec
if errorlevel 1 (
  echo [!] Core build failed. See the log above.
  pause
  exit /b 1
)
if /i not "%MODE%"=="all" goto :done

:done
echo.
echo Built:
echo   ..\build\SecondBrain\SecondBrain.exe
echo   ..\build-core\SecondBrain-Core\SecondBrain-Core.exe
echo.
echo Ship the whole folder (exe + _internal). Put it next to brain.db, double-click the exe.
pause
