"""
sheets.py — Google Sheets read/write manager
"""

import os
import logging
from datetime import date
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Sheet tab names — must match your actual Google Sheet
COMPANY_SHEET = "Company Tracker"
OUTREACH_SHEET = "Outreach Tracker"

# Column indices (1-based) for Company Tracker
COL_COMPANY_NAME = 2          # B
COL_COMPANY_TIER = 3          # C
COL_COMPANY_HQ = 4            # D
COL_COMPANY_WHY = 5           # E
COL_COMPANY_PRIORITY = 8      # H
COL_COMPANY_STATUS = 9        # I
COL_COMPANY_PEOPLE = 11       # K
COL_COMPANY_EMAILS_SENT = 12  # L

# Column indices for Outreach Tracker
# A:R = original columns, S-W = new v2 columns
OUTREACH_COLS = [
    "#",
    "First Name",
    "Last Name",
    "Company",
    "Role / Title",
    "LinkedIn URL",
    "Email",
    "Email Source",
    "Hook (one-liner)",
    "Status",
    "Date Sent",
    "Days Since Sent",
    "Opened?",
    "Replied?",
    "Follow-up Sent?",
    "Outcome",
    "Notes",
    "Subject",  # col R (index 17) — internal use for follow-ups
    "Reply Type",  # col S (index 18) — positive/rejection/referral/etc.
    "Suggested Reply",  # col T (index 19) — agent-drafted, NOT auto-sent
    "Thread ID",  # col U (index 20) — Gmail message ID
    "Lead Score",  # col V (index 21) — numeric score
    "Do Not Contact",  # col W (index 22) — Yes/No
]
COL_SUBJECT_IDX = 17


def get_creds():
    """
    Load OAuth credentials from .creds/token.pickle.
    FIX #5: Wraps token refresh in try/except so an expired/revoked token
    falls through gracefully to a fresh InstalledAppFlow re-auth instead of
    crashing the process.
    """
    creds = None

    # Use absolute paths so cron jobs don't fail
    base_dir = os.path.dirname(os.path.abspath(__file__))
    creds_dir = os.path.join(base_dir, ".creds")
    os.makedirs(creds_dir, exist_ok=True)

    token_path = os.path.join(creds_dir, "token.pickle")
    creds_path = os.path.join(creds_dir, "credentials.json")

    if os.path.exists(token_path):
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log.warning(f"Token refresh failed ({e}). Re-running OAuth flow...")
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Missing {creds_path}. Please place your Google OAuth credentials.json inside the .creds folder."
                )

            if "PYTHONANYWHERE_SITE" in os.environ:
                raise RuntimeError(
                    "Headless environment detected (PythonAnywhere). "
                    "Cannot open a browser window for OAuth here.\n"
                    "Fix: Run this script once on your laptop to generate '.creds/token.pickle', "
                    "then upload that token.pickle file to PythonAnywhere!"
                )

            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "wb") as f:
            pickle.dump(creds, f)

    return creds


class SheetManager:
    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        creds = get_creds()
        self.service = build("sheets", "v4", credentials=creds)
        self.sheet = self.service.spreadsheets()

    def _handle_error(self, e):
        err_msg = str(e)
        if "Office file" in err_msg or "not supported for this document" in err_msg:
            log.critical(
                "\n"
                "========================================================================\n"
                "❌ GOOGLE SHEET CONFIGURATION ERROR:\n"
                "Your SPREADSHEET_ID points to an Excel (.xlsx) file in Google Drive!\n"
                "The Google Sheets API does not support writing directly to Excel files.\n\n"
                "HOW TO FIX THIS:\n"
                "1. Open the file in Google Sheets web interface (https://docs.google.com/spreadsheets).\n"
                "2. Click 'File' -> 'Save as Google Sheets' in the top menu.\n"
                "3. This will create a native Google Sheet. Copy the NEW spreadsheet ID from the URL:\n"
                "   https://docs.google.com/spreadsheets/d/NEW_SPREADSHEET_ID/edit\n"
                "4. Update the SPREADSHEET_ID in your '.env' file with the new ID.\n"
                "========================================================================\n"
            )

    def _read(self, range_: str):
        try:
            result = (
                self.sheet.values()
                .get(spreadsheetId=self.spreadsheet_id, range=range_)
                .execute()
            )
            return result.get("values", [])
        except Exception as e:
            self._handle_error(e)
            raise

    def _write(self, range_: str, values: list):
        try:
            self.sheet.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
        except Exception as e:
            self._handle_error(e)
            raise

    def _append(self, range_: str, values: list):
        try:
            self.sheet.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=range_,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()
        except Exception as e:
            self._handle_error(e)
            raise

    def get_company_rows(self) -> list[dict]:
        # Open-ended range — scales to any number of companies
        data = self._read(f"'{COMPANY_SHEET}'!A5:M")
        rows = []
        for i, row in enumerate(data):

            def g(idx, default="", r=row):
                return r[idx].strip() if len(r) > idx else default

            rows.append(
                {
                    "_row_index": i + 5,  # actual sheet row number
                    "Company": g(COL_COMPANY_NAME - 1),
                    "Tier": g(COL_COMPANY_TIER - 1),
                    "HQ": g(COL_COMPANY_HQ - 1),
                    "Why Target": g(COL_COMPANY_WHY - 1),
                    "Priority": g(COL_COMPANY_PRIORITY - 1),
                    "Status": g(COL_COMPANY_STATUS - 1),
                    "People Found": g(COL_COMPANY_PEOPLE - 1),
                    "Emails Sent": g(COL_COMPANY_EMAILS_SENT - 1, "0"),
                }
            )
        return [r for r in rows if r["Company"]]

    def get_outreach_rows(self) -> list[dict]:
        # Open-ended range — reads all columns
        data = self._read(f"'{OUTREACH_SHEET}'!A5:W")
        rows = []
        for i, row in enumerate(data):

            def g(idx, default="", r=row):
                return r[idx].strip() if len(r) > idx else default

            rows.append(
                {
                    "_row_index": i + 5,
                    "First Name": g(1),
                    "Last Name": g(2),
                    "Company": g(3),
                    "Role / Title": g(4),
                    "LinkedIn URL": g(5),
                    "Email": g(6),
                    "Email Source": g(7),
                    "Hook": g(8),
                    "Status": g(9),
                    "Date Sent": g(10),
                    "Opened?": g(12),
                    "Replied?": g(13),
                    "Follow-up Sent?": g(14),
                    "Outcome": g(15),
                    "Notes": g(16),
                    "Subject": g(17),
                    "Reply Type": g(18),
                    "Suggested Reply": g(19),
                    "Thread ID": g(20),
                    "Lead Score": g(21),
                    "Do Not Contact": g(22),
                }
            )
        return [r for r in rows if r["First Name"]]

    def append_outreach_row(self, data: dict):
        existing = self.get_outreach_rows()
        next_num = len(existing) + 1
        days_formula = '=IF(INDIRECT("K"&ROW())="","",TODAY()-INDIRECT("K"&ROW()))'
        row = [
            next_num,
            data.get("First Name", ""),
            data.get("Last Name", ""),
            data.get("Company", ""),
            data.get("Role / Title", ""),
            data.get("LinkedIn URL", ""),
            data.get("Email", ""),
            data.get("Email Source", "Agent"),
            data.get("Hook", ""),
            data.get("Status", "Sent"),
            data.get("Date Sent", date.today().isoformat()),
            days_formula,
            data.get("Opened?", "No"),
            data.get("Replied?", "No"),
            data.get("Follow-up Sent?", "No"),
            data.get("Outcome", "Awaiting reply"),
            data.get("Notes", ""),
            data.get("Subject", ""),  # col R
            data.get("Reply Type", ""),  # col S
            data.get("Suggested Reply", ""),  # col T
            data.get("Thread ID", ""),  # col U
            data.get("Lead Score", ""),  # col V
            data.get("Do Not Contact", "No"),  # col W
        ]
        self._append(f"'{OUTREACH_SHEET}'!A:W", [row])
        log.info(
            f"Sheet row appended: {data.get('First Name')} @ {data.get('Company')}"
        )

    def update_followup_sent(self, row_index: int, today: str):
        self._write(f"'{OUTREACH_SHEET}'!O{row_index}", [["Yes"]])
        log.info(f"Follow-up marked sent on row {row_index}")

    def update_company_emails_sent(self, row_index: int):
        data = self._read(f"'{COMPANY_SHEET}'!L{row_index}")
        current = 0
        if data and data[0]:
            try:
                current = int(data[0][0])
            except ValueError:
                current = 0
        self._write(f"'{COMPANY_SHEET}'!L{row_index}", [[current + 1]])

    def update_people_found(self, row_index: int, contacts_text: str):
        """Write auto-discovered contacts to column K ("People Found") of Company Tracker."""
        self._write(f"'{COMPANY_SHEET}'!K{row_index}", [[contacts_text]])
        log.info(f"People Found written to row {row_index} in {COMPANY_SHEET}")

    def update_opened(self, email: str):
        """Call this if you integrate open tracking."""
        rows = self.get_outreach_rows()
        for r in rows:
            if r["Email"].lower() == email.lower() and r["Opened?"] != "Yes":
                self._write(f"'{OUTREACH_SHEET}'!M{r['_row_index']}", [["Yes"]])
                log.info(f"Marked opened: {email}")
                break

    def update_replied(self, email: str):
        """Mark the most recent outreach row for this email as Replied."""
        rows = self.get_outreach_rows()
        for r in rows:
            if r["Email"].lower() == email.lower() and r["Replied?"] != "Yes":
                self._write(f"'{OUTREACH_SHEET}'!N{r['_row_index']}", [["Yes"]])
                self._write(f"'{OUTREACH_SHEET}'!P{r['_row_index']}", [["Replied"]])
                log.info(f"Marked replied: {email}")
                break

    def update_reply_classification(
        self, email: str, reply_type: str, suggested_reply: str
    ):
        """
        Write reply classification and suggested response to the sheet.
        Does NOT auto-send — just saves it for manual review.
        """
        rows = self.get_outreach_rows()
        for r in rows:
            if r["Email"].lower() == email.lower() and r["Replied?"] == "Yes":
                self._write(f"'{OUTREACH_SHEET}'!S{r['_row_index']}", [[reply_type]])
                self._write(
                    f"'{OUTREACH_SHEET}'!T{r['_row_index']}", [[suggested_reply]]
                )
                # Mark as Do Not Contact if they asked
                if reply_type in ("do_not_contact", "rejection"):
                    self._write(f"'{OUTREACH_SHEET}'!W{r['_row_index']}", [["Yes"]])
                log.info(f"Reply classified for {email}: {reply_type}")
                break
