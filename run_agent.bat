@echo off
REM run_agent.bat — Used by Windows Task Scheduler to trigger the agent silently.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo =======================================================
echo Outreach Agent is starting up...
echo =======================================================
echo.

REM Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

REM Get robust date formatted as YYYY-MM-DD (independent of Windows regional format)
for /f %%i in ('powershell -Command "Get-Date -Format 'yyyy-MM-dd'"') do set TODAY=%%i

REM Check if we already ran successfully today to prevent duplicate runs
if exist logs\last_run_date.txt (
    set /p LAST_RUN=<logs\last_run_date.txt
    if "!LAST_RUN!"=="!TODAY!" (
        echo [INFO] Outreach Agent already ran today !TODAY!. Skipping run...
        timeout /t 5 >nul
        exit /b 0
    )
)

REM Set Python to UTF-8 mode to handle Unicode characters in logs/emails
set PYTHONUTF8=1

REM Run the agent and save timestamped log
set LOGFILE=logs\run_!TODAY!.log

echo [INFO] The agent is now running.
echo [INFO] It usually takes 15-30 minutes to discover leads, find contacts, and send emails.
echo [INFO] You can safely minimize or leave this window open. It will close automatically when finished.
echo [INFO] DO NOT CLOSE THIS WINDOW manually, or emails will not be sent.
echo.
echo [INFO] Logging output to: !LOGFILE!
echo [INFO] Please wait...

python agent.py >> "!LOGFILE!" 2>&1

REM If python succeeded or ran, update the lock file so we don't run again today
if %errorlevel% equ 0 (
    echo !TODAY!> logs\last_run_date.txt
    echo [SUCCESS] Agent finished successfully!
) else (
    echo [ERROR] Agent execution failed. Will allow retry on next trigger. >> "!LOGFILE!"
    echo [ERROR] Agent encountered an error. Check !LOGFILE! for details.
)

timeout /t 10 >nul
