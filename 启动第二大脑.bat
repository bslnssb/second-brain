@echo off
chcp 65001 >nul
rem 中文名入口，方便双击；实际逻辑在 start.bat，两个文件完全等价。
call "%~dp0start.bat"
