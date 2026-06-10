"""
mailer.py — Gmail API sender (individual emails, no BCC)
"""

import os
import re
import base64
import logging
import pickle
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

SENDER_NAME = os.environ.get("YOUR_NAME", "Your Name")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")  # your Gmail


def get_gmail_creds():
    """
    Load OAuth credentials from .creds/token.pickle.
    Wraps token refresh in try/except so an expired/revoked token
    falls through gracefully to a fresh InstalledAppFlow re-auth.
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


class GmailSender:
    def __init__(self):
        creds = get_gmail_creds()
        self.service = build("gmail", "v1", credentials=creds)

    def send(self, to: str, subject: str, body: str):
        if not to or "@" not in to:
            raise ValueError(f"Invalid email: {to}")

        msg = MIMEText(body, "html")
        msg["to"] = to
        msg["from"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info(f"Email sent -> {to} | Subject: {subject}")

    def check_replies(self, known_emails: set | None = None) -> list[dict]:
        """
        Returns list of dicts with 'email' and 'body' for replies to your outreach.

        Scans last 50 inbox messages. Only returns emails that are in our
        known_emails set (avoids false positives from unrelated inbox replies).

        Returns:
            [{"email": "person@company.com", "body": "Thanks for reaching out..."}]
        """
        try:
            results = (
                self.service.users()
                .messages()
                .list(userId="me", labelIds=["INBOX"], q="in:inbox")
                .execute()
            )
        except Exception as e:
            log.error(f"Failed to list inbox messages: {e}")
            return []

        replied = []
        seen_emails = set()
        messages = results.get("messages", [])

        for msg_ref in messages[:50]:
            try:
                msg = (
                    self.service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=msg_ref["id"],
                        format="full",
                        metadataHeaders=["From", "In-Reply-To", "References"],
                    )
                    .execute()
                )

                headers = {
                    h["name"].lower(): h["value"] for h in msg["payload"]["headers"]
                }
                from_header = headers.get("from", "")
                is_reply = bool(headers.get("in-reply-to") or headers.get("references"))

                if is_reply and "@" in from_header:
                    match = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]+", from_header)
                    if match:
                        addr = match.group(0).lower()
                        if addr in seen_emails:
                            continue
                        # Only record if we know this contact (avoid noise)
                        if known_emails is None or addr in known_emails:
                            # Extract the reply body text
                            body_text = self._extract_body(msg["payload"])
                            replied.append({"email": addr, "body": body_text})
                            seen_emails.add(addr)
            except Exception as e:
                log.warning(f"Error reading message {msg_ref.get('id')}: {e}")

        return replied

    def _extract_body(self, payload: dict) -> str:
        """Extract plain text body from a Gmail message payload."""
        # Try direct body
        if payload.get("body", {}).get("data"):
            try:
                return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                pass

        # Try parts (multipart messages)
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get(
                "data"
            ):
                try:
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    pass
            # Recurse into nested parts
            if part.get("parts"):
                result = self._extract_body(part)
                if result:
                    return result

        return "(Could not extract reply body)"

    def get_all_sent_emails(self, max_results=200) -> set[str]:
        """Fetch a set of all email addresses the user has recently sent emails to."""
        sent_emails = set()
        try:
            results = self.service.users().messages().list(userId="me", labelIds=["SENT"], maxResults=max_results).execute()
            messages = results.get("messages", [])
            for msg_ref in messages:
                msg = self.service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="metadata", metadataHeaders=["To"]
                ).execute()
                for h in msg.get("payload", {}).get("headers", []):
                    if h["name"].lower() == "to":
                        # Extract email address
                        match = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]+", h["value"])
                        if match:
                            sent_emails.add(match.group(0).lower())
        except Exception as e:
            log.warning(f"Failed to fetch sent emails from Gmail: {e}")
        return sent_emails

    def check_bounces(self) -> list[str]:
        """Check for Address Not Found / bounced emails from mailer-daemon."""
        bounced_emails = []
        try:
            results = self.service.users().messages().list(
                userId="me", q="from:mailer-daemon@googlemail.com OR subject:\"Delivery Status Notification (Failure)\"", maxResults=50
            ).execute()
            messages = results.get("messages", [])
            for msg_ref in messages:
                msg = self.service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()
                body_text = self._extract_body(msg["payload"])
                
                # Bounces usually say "Address not found" and then list the email, or "Message not delivered to X"
                # Looking for standard patterns:
                matches = re.findall(r"not delivered to ([\w.+-]+@[\w-]+\.[a-zA-Z]+)", body_text, re.IGNORECASE)
                if not matches:
                    matches = re.findall(r"Address not found\s*Your message wasn't delivered to ([\w.+-]+@[\w-]+\.[a-zA-Z]+)", body_text, re.IGNORECASE)
                if not matches:
                    matches = re.findall(r"The response from the remote server was:\s*(?:550|554).*?([\w.+-]+@[\w-]+\.[a-zA-Z]+)", body_text, re.IGNORECASE)
                if not matches:
                    matches = re.findall(r"Failed to deliver to '([\w.+-]+@[\w-]+\.[a-zA-Z]+)'", body_text, re.IGNORECASE)
                if not matches:
                    # Generic fallback looking for any email surrounded by asterisks or quotes near failure text
                    if "delivery to the following recipient failed permanently" in body_text.lower() or "address not found" in body_text.lower():
                        matches = re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]+", body_text)
                        # We might pick up our own email, so we will filter later or rely on the fact that we sent to them

                if matches:
                    bounced_emails.extend([m.lower() for m in matches if m.lower() != SENDER_EMAIL.lower()])
        except Exception as e:
            log.error(f"Failed to check bounces: {e}")
            
        return list(set(bounced_emails))
