@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 convert_extracted_thorax_dicoms_to_nrrd.py
) else (
    python convert_extracted_thorax_dicoms_to_nrrd.py
)

echo.
pause
