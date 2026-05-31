@echo off
:: run_agent.bat — Used by Windows Task Scheduler to trigger the agent silently.
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

:: Get robust date formatted as YYYY-MM-DD (independent of Windows regional format)
for /f %%i in ('powershell -Command "Get-Date -Format 'yyyy-MM-dd'"') do set TODAY=%%i

:: Check if we already ran successfully today to prevent duplicate runs
if exist logs\last_run_date.txt (
    set /p LAST_RUN=<logs\last_run_date.txt
    if "!LAST_RUN!"=="!TODAY!" (
        echo [INFO] Outreach Agent already ran today (!TODAY!). Skipping run...
        exit /b 0
    )
)

:: Load .env variables (skip blank lines and comment lines starting with #)
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /v "^#" .env`) do (
    if not "%%A"=="" if not "%%B"=="" (
        set "%%A=%%B"
    )
)

:: Run the agent and save timestamped log
set LOGFILE=logs\run_!TODAY!.log
python agent.py >> "!LOGFILE!" 2>&1

:: If python succeeded or ran, update the lock file so we don't run again today
if %errorlevel% equ 0 (
    echo !TODAY!> logs\last_run_date.txt
) else (
    :: Also write to log that it failed, but let it retry next time
    echo [ERROR] Agent execution failed. Will allow retry on next trigger. >> "!LOGFILE!"
)

