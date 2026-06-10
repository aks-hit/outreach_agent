@echo off
:: ============================================================================
:: setup.bat — One-click setup for Outreach Agent
:: Run this ONCE after downloading the project. It will:
::   1. Verify Python is installed
::   2. Install all Python dependencies
::   3. Validate .env (required keys filled in)
::   4. Check Hunter.io key (optional — warns but does not fail)
::   5. Check credentials.json exists
::   6. Register Windows Task Scheduler at User Logon
:: ============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo  ==========================================
echo    Outreach Agent — One-Click Setup
echo  ==========================================
echo.

set PASS=0
set FAIL=0
set WARN=0

:: ── Step 1: Check Python ─────────────────────────────────────────────────────
echo [1/6] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] Python not found. Install Python 3.10+ from https://python.org
    set /a FAIL+=1
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
    echo   [PASS] !PY_VER! found.
    set /a PASS+=1
)

:: ── Step 2: Install dependencies ─────────────────────────────────────────────
echo.
echo [2/7] Installing Python dependencies from requirements.txt...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo   [FAIL] pip install failed. Check internet connection or run manually:
    echo          pip install -r requirements.txt
    set /a FAIL+=1
) else (
    echo   [PASS] All dependencies installed successfully.
    set /a PASS+=1
)

:: ── Step 2b: Install Playwright browser ──────────────────────────────────────
echo.
echo [2b/7] Installing Playwright Chromium browser for LinkedIn scraping...
playwright install chromium >nul 2>&1
if %errorlevel% neq 0 (
    echo   [WARN] Playwright browser install failed. LinkedIn scraping will be disabled.
    echo          Run manually: playwright install chromium
    set /a WARN+=1
) else (
    echo   [PASS] Playwright Chromium browser installed.
    set /a PASS+=1
)

:: ── Step 3: Validate .env required keys ──────────────────────────────────────
echo.
echo [3/6] Checking .env required configuration...
if not exist ".env" (
    echo   [FAIL] .env not found. Run: copy .env.example .env  then fill it in.
    set /a FAIL+=1
    goto :check_hunter
)

set ENV_OK=1

findstr /C:"your_gemini_api_key_here" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo   [FAIL] GEMINI_API_KEY is still the placeholder. Edit .env
    set /a FAIL+=1
    set ENV_OK=0
)

findstr /C:"your_google_sheet_id_here" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo   [FAIL] SPREADSHEET_ID is still the placeholder. Edit .env
    set /a FAIL+=1
    set ENV_OK=0
)

findstr /C:"your.gmail@gmail.com" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo   [FAIL] SENDER_EMAIL is still the placeholder. Edit .env
    set /a FAIL+=1
    set ENV_OK=0
)

if !ENV_OK! equ 1 (
    echo   [PASS] .env required keys are set.
    set /a PASS+=1
)

:check_hunter
:: ── Step 4: Check Hunter.io API key (optional) ───────────────────────────────
echo.
echo [4/6] Checking Hunter.io API key (optional — for auto contact discovery)...
if not exist ".env" goto :check_creds

findstr /C:"your_hunter_io_api_key_here" .env >nul 2>&1
if %errorlevel% equ 0 (
    echo   [WARN] HUNTER_API_KEY not set.
    echo          Contact auto-discovery is DISABLED.
    echo          Get a free key: https://hunter.io ^> sign up ^> API
    echo          Then add HUNTER_API_KEY=yourkey to .env
    set /a WARN+=1
) else (
    findstr /C:"HUNTER_API_KEY=" .env >nul 2>&1
    if %errorlevel% equ 0 (
        echo   [PASS] HUNTER_API_KEY is set — contact auto-discovery enabled.
        set /a PASS+=1
    ) else (
        echo   [WARN] HUNTER_API_KEY line not in .env.
        echo          Add: HUNTER_API_KEY=yourkey  to enable auto contact discovery.
        set /a WARN+=1
    )
)

:check_creds
:: ── Step 5: Check credentials.json ───────────────────────────────────────────
echo.
echo [5/6] Checking credentials.json (Google Cloud OAuth)...
set CREDS_FOUND=0
if exist "credentials.json" set CREDS_FOUND=1
if exist ".creds\credentials.json" set CREDS_FOUND=1

if !CREDS_FOUND! equ 0 (
    echo   [FAIL] credentials.json not found.
    echo          Google Cloud Console ^> APIs ^& Services ^> Credentials ^> Create OAuth Client ID
    echo          Application type: Desktop app ^> Download JSON ^> rename to credentials.json and place in .creds/
    set /a FAIL+=1
) else (
    echo   [PASS] credentials.json found.
    set /a PASS+=1
)

:: ── Step 6: Register Windows Task Scheduler ──────────────────────────────────
echo.
echo [6/6] Registering Windows Task Scheduler (at User Logon, once a day)...

set TASK_NAME=OutreachAgent
set SCRIPT_PATH=%~dp0run_agent.bat

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command "$action = New-ScheduledTaskAction -Execute '%SCRIPT_PATH%'; $trigger = New-ScheduledTaskTrigger -AtLogon; $trigger.Delay = 'PT1M'; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force" >nul 2>&1


if %errorlevel% neq 0 (
    echo   [FAIL] Could not register Task Scheduler.
    echo          Right-click setup.bat ^> Run as administrator and try again.
    set /a FAIL+=1
) else (
    echo   [PASS] Task Scheduler job '%TASK_NAME%' registered — runs at Windows Logon with once a day lock.
    set /a PASS+=1
)

:: ── Summary ───────────────────────────────────────────────────────────────────
echo.
echo  ==========================================
echo    Setup Result: !PASS! passed, !FAIL! failed, !WARN! warnings
echo  ==========================================
echo.

if !FAIL! gtr 0 (
    echo  Fix the [FAIL] items above, then run setup.bat again.
    echo.
) else (
    echo  Core setup complete!
    echo.
    if !WARN! gtr 0 (
        echo  [Optional] Add HUNTER_API_KEY to .env for auto contact discovery.
        echo             Get it free at https://hunter.io
        echo.
    )
    echo  Next steps:
    echo    1. python agent.py                  ^<-- first-time Gmail OAuth login and test run
    echo    2. Check agent.log + Outreach Tracker tab in your Google Sheet
    echo.
    echo  After that, the agent runs automatically whenever you log into Windows - limited to once per day.
    echo  You can close this terminal — Windows Task Scheduler handles it.
    echo.
)

pause
endlocal
