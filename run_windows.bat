@echo off
REM YILDIZ Ders Otomasyonu - Windows Launcher
REM v1.0 (with venv support)

echo ========================================
echo YILDIZ Ders Otomasyonu v1.0
echo ========================================
echo.

REM Change to script directory
cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found!
    echo.
    echo Please install Python 3.9+ from:
    echo https://www.python.org/downloads/
    echo.
    echo Or use winget:
    echo   winget install Python.Python.3.12
    echo.
    pause
    exit /b 1
)

echo [OK] Python found
echo.

REM Virtual environment directory
set VENV_DIR=venv

REM Check if venv exists, if not create it
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv %VENV_DIR%
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
    echo.

    REM Upgrade pip immediately after venv creation
    echo [INFO] Upgrading pip...
    call %VENV_DIR%\Scripts\activate.bat
    python.exe -m pip install --upgrade pip
    echo [OK] pip upgraded
    echo.
)

REM Activate virtual environment
echo [INFO] Activating virtual environment...
call %VENV_DIR%\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment activated
echo.

REM Check if dependencies need to be installed
echo Checking dependencies...
pip show requests beautifulsoup4 lxml >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
)

echo [OK] Dependencies ready
echo.
echo Starting automation...
echo.

REM Run main.py
python main.py

REM Pause if error occurred
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Program exited with error code %errorlevel%
    pause
)

REM Deactivate venv on exit
deactivate 2>nul
