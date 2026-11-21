@echo off
chcp 65001 >nul
title Second Brain - report a problem
echo.
echo Describe the problem in one line, then press Enter.
echo Leave empty to send a generic report with environment info.
echo.
set "MSG="
set /p "MSG="
"%~dp0SecondBrain.exe" --feedback "%MSG%"
echo.
pause
