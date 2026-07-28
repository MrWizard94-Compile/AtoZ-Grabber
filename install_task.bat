@echo off
echo ============================================================
echo  A to Z Shift Grabber - Task Scheduler Setup
echo ============================================================
echo.

:: Get the directory this script is in
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Find Python path
for /f "tokens=*" %%i in ('where python 2^>nul') do set "PYTHON_PATH=%%i" & goto :found_python
echo ERROR: Python not found in PATH.
pause
exit /b 1
:found_python

echo Script directory: %SCRIPT_DIR%
echo Python path: %PYTHON_PATH%
echo.

:: Launch at 11:55 — gives time for login before 11:59 poll start
set "START_TIME=11:55"

echo Creating scheduled task: AtoZ_ShiftGrabber
echo   Trigger: Daily at %START_TIME%
echo   Action:  python atoz_grabber.py run
echo.

:: Delete existing task if present
schtasks /Delete /TN "AtoZ_ShiftGrabber" /F >nul 2>&1

:: Create the scheduled task
schtasks /Create ^
    /TN "AtoZ_ShiftGrabber" ^
    /TR "\"%PYTHON_PATH%\" \"%SCRIPT_DIR%\atoz_grabber.py\" run" ^
    /SC DAILY ^
    /ST %START_TIME% ^
    /RL HIGHEST ^
    /F

if errorlevel 1 (
    echo.
    echo ERROR: Failed to create scheduled task.
    echo Try running this script as Administrator.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Task created successfully!
echo ============================================================
echo.
echo  The grabber will run DAILY at %START_TIME% AM.
echo  It will:
echo    1. Launch at 11:55 AM
echo    2. Log in to A to Z (automated)
echo    3. Navigate to the +7 day tab
echo    4. Wait until 11:59 AM
echo    5. Start polling for new shifts
echo    6. Grab matching shifts the instant they appear
echo    7. Send you a Windows notification with results
echo.
echo  To check the task:
echo    schtasks /Query /TN "AtoZ_ShiftGrabber" /V
echo.
echo  To delete the task:
echo    schtasks /Delete /TN "AtoZ_ShiftGrabber" /F
echo.
echo  To run it manually right now:
echo    schtasks /Run /TN "AtoZ_ShiftGrabber"
echo.
echo ============================================================
pause
