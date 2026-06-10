@echo off
setlocal
cd /d "%~dp0"

echo.
echo  ================================================
echo    Outreach Agent — Reset LinkedIn Account
echo  ================================================
echo.

echo [1/2] Clearing existing LinkedIn session data...
if exist ".creds\linkedin_state.json" (
    del /f ".creds\linkedin_state.json"
    echo   Deleted: .creds\linkedin_state.json
)
if exist ".creds\linkedin_profile" (
    rmdir /s /q ".creds\linkedin_profile"
    echo   Deleted: .creds\linkedin_profile folder
)

echo.
echo [2/2] Launching interactive login window...
echo.
python -c "from linkedin_scraper import LinkedInScraper; LinkedInScraper().login_interactive()"

echo.
echo LinkedIn account change complete!
echo.
pause
endlocal
