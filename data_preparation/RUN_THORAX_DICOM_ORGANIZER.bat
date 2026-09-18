@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 organize_thorax_dicoms.py
) else (
    python organize_thorax_dicoms.py
)

echo.
pause
