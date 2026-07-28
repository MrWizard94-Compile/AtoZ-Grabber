@echo off
echo ============================================================
echo  A to Z Shift Grabber - Setup
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo [1/4] Installing Python dependencies...
pip install playwright winotify --break-system-packages -q
if errorlevel 1 (
    echo ERROR: Failed to install Python packages.
    pause
    exit /b 1
)

echo [2/4] Installing Playwright browsers...
python -m playwright install chromium
if errorlevel 1 (
    echo ERROR: Failed to install Playwright browsers.
    pause
    exit /b 1
)

echo [3/4] Creating browser session directory...
if not exist "browser_session" mkdir browser_session

echo [4/4] Setup complete!
echo.
echo ============================================================
echo  NEXT STEPS:
echo ============================================================
echo.
echo  1. Run the login command to save your A to Z session:
echo.
echo     python atoz_grabber.py login
echo.
echo     This opens a browser window. Log in to A to Z normally.
echo     Your session will be saved for future automated runs.
echo.
echo  2. Test the grabber on today's shifts:
echo.
echo     python atoz_grabber.py test --headed
echo.
echo  3. Install the daily Task Scheduler job:
echo.
echo     install_task.bat
echo.
echo  4. (Optional) Add blackout days:
echo.
echo     python atoz_grabber.py blackout add Saturday
echo     python atoz_grabber.py blackout add 2026-03-25
echo     python atoz_grabber.py blackout list
echo.
echo ============================================================
pause
