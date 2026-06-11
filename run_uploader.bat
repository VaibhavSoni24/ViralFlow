@echo off
setlocal

:: Get the directory where this batch file is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ====================================================
echo ViralFlow - Automated Social Media Publisher
echo ====================================================

:: Check if virtual environment exists, if so activate it
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment...
    call "venv\Scripts\activate.bat"
) else (
    echo [INFO] No venv found. Using system Python...
)

echo [INFO] Starting uploader script...
python uploader.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if %EXIT_CODE% equ 0 (
    echo [SUCCESS] Script completed successfully!
) else (
    echo [ERROR] Script finished with error code %EXIT_CODE%. Check uploader.log for details.
)

echo ====================================================
echo Closing in 3 seconds...
timeout /t 3 /nobreak >nul
exit /b %EXIT_CODE%
