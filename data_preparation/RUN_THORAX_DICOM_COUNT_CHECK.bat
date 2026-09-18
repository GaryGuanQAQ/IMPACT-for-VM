@echo off
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 data_preparation\count_extracted_thorax_dicoms.py
) else (
    python data_preparation\count_extracted_thorax_dicoms.py
)

echo.
pause
