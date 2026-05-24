@echo off
:: run_agent.bat — Used by Windows Task Scheduler to trigger the agent silently.
:: FIX: Previous version had broken .env parser (%%A:~0,1%% syntax error).
::      Now uses a correct parser that skips comment lines starting with #
::      and handles values containing = signs (e.g. API keys).

cd /d "%~dp0"

:: Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

:: Load .env variables (skip blank lines and comment lines starting with #)
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /v "^#" .env`) do (
    if not "%%A"=="" if not "%%B"=="" (
        set "%%A=%%B"
    )
)

:: Run the agent and save timestamped log
set LOGFILE=logs\run_%date:~10,4%%date:~4,2%%date:~7,2%.log
python agent.py >> "%LOGFILE%" 2>&1
