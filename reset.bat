@echo off
:: ============================================================================
:: reset.bat — Clean reset for Outreach Agent
:: Use this when you want to:
::   - Switch Gmail accounts
::   - Force a fresh OAuth login (e.g. after token.pickle expiry)
::   - Remove the Task Scheduler job
:: ============================================================================
setlocal

cd /d "%~dp0"

echo.
echo  ==========================================
echo    Outreach Agent — Reset / Re-Auth
echo  ==========================================
echo.

:: Remove Windows Task Scheduler job
echo [1/2] Removing Windows Task Scheduler job 'OutreachAgent'...
schtasks /delete /tn "OutreachAgent" /f >nul 2>&1
if %errorlevel% equ 0 (
    echo   Done. Task removed.
) else (
    echo   Task was not registered (nothing to remove).
)

:: Delete token.pickle
echo.
echo [2/3] Deleting token.pickle (OAuth credentials)...
if exist ".creds\token.pickle" (
    del /f ".creds\token.pickle"
    echo   Done. token.pickle deleted.
) else (
    echo   token.pickle not found (already clean).
)

:: Delete LinkedIn session cookies and profile
echo.
echo [3/3] Deleting LinkedIn session cookies and profile...
if exist ".creds\linkedin_state.json" (
    del /f ".creds\linkedin_state.json"
    echo   Done. linkedin_state.json deleted.
)
if exist ".creds\linkedin_profile" (
    rmdir /s /q ".creds\linkedin_profile"
    echo   Done. linkedin_profile directory deleted.
)


echo.
echo  Reset complete!
echo.
echo  To re-register and re-authenticate:
echo    1. Run: setup.bat         (re-registers Task Scheduler + validates config)
echo    2. Run: python agent.py   (opens browsers for Gmail OAuth + LinkedIn login)
echo.
pause
endlocal
