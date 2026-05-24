"""
agent.py — Gemini-powered cold outreach agent v2
Runs daily: syncs + classifies replies, discovers leads, scores contacts,
generates personalized emails (single Gemini call), sends them, and updates the sheet.
"""

from dotenv import load_dotenv

load_dotenv()
import os
import time
import json
import logging
from datetime import datetime, date
import zoneinfo
from typing import Optional
from google import genai
from sheets import SheetManager
from mailer import GmailSender
from contact_finder import ContactFinder
from lead_generator import LeadGeneratorAgent


class QuotaExhaustedError(Exception):
    pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("agent.log"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
YOUR_NAME = os.environ.get("YOUR_NAME", "[Your Name]")
YOUR_LINKEDIN = os.environ.get("YOUR_LINKEDIN", "linkedin.com/in/yourprofile")
EMAILS_PER_DAY = int(os.environ.get("EMAILS_PER_DAY", "10"))
FOLLOW_UP_DAYS = int(os.environ.get("FOLLOW_UP_DAYS", "5"))
HUNTER_MAX_SEARCHES = int(os.environ.get("HUNTER_MAX_SEARCHES", "100"))
MIN_LEAD_SCORE = int(os.environ.get("MIN_LEAD_SCORE", "6"))
CAMPAIGN_START_DATE = os.environ.get("CAMPAIGN_START_DATE", date.today().isoformat())
YOUR_RESUME = os.environ.get("YOUR_RESUME", "https://your-resume-link-here.com")

# Read profile summary from file
profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.txt")
try:
    with open(profile_path, "r", encoding="utf-8") as f:
        PROFILE_SUMMARY = f.read().strip()
except FileNotFoundError:
    log.warning(f"{profile_path} not found! Please create it from profile.txt.example.")
    PROFILE_SUMMARY = "Software Engineer looking for roles."

# ── Gemini setup ──────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)


def gemini(prompt: str, retries=4) -> str:
    """Call Gemini with rate-limiting and exponential backoff retry."""
    time.sleep(4)
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = any(
                x in err_str
                for x in ["429", "resource_exhausted", "quota exceeded", "rate limit"]
            )

            if is_rate_limit and "quota exceeded" in err_str and "requests" in err_str:
                raise QuotaExhaustedError(f"Daily Gemini Quota Exhausted: {e}")

            if attempt < retries - 1:
                if is_rate_limit:
                    sleep_time = 20 * (attempt + 1)
                    log.warning(f"Gemini rate limit hit. Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                else:
                    sleep_time = 5 * (attempt + 1)
                    log.warning(
                        f"Gemini error. Retrying in {sleep_time}s... Error: {e}"
                    )
                    time.sleep(sleep_time)
            else:
                log.error(
                    f"Gemini call failed permanently after {retries} attempts: {e}"
                )
                if is_rate_limit:
                    raise QuotaExhaustedError(f"Gemini Rate Limit Exhausted: {e}")
                raise
    return ""


def extract_json(raw: str) -> dict:
    """Parse JSON from Gemini response, stripping markdown wrappers if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return json.loads(raw)


# ── Daily send ramp schedule ─────────────────────────────────────────────────
def get_daily_limit() -> int:
    """Ramp email volume to protect Gmail deliverability."""
    try:
        start = date.fromisoformat(CAMPAIGN_START_DATE)
    except ValueError:
        start = date.today()
    days_active = (date.today() - start).days
    if days_active < 7:
        return 10  # Week 1: warm up
    elif days_active < 21:
        return 25  # Week 2-3: ramp
    else:
        return 50  # Week 4+: cruise


# ── Lead scoring (local, no Gemini call) ─────────────────────────────────────
AI_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "ml",
    "llm",
    "deep learning",
    "nlp",
    "natural language",
    "computer vision",
    "rag",
    "generative",
    "gpt",
    "transformer",
    "neural",
    "data science",
    "langchain",
    "agent",
    "agentic",
]

TARGET_CITIES = [
    "bengaluru",
    "bangalore",
    "hyderabad",
    "gurgaon",
    "gurugram",
    "pune",
    "san francisco",
    "silicon valley",
    "new york",
    "london",
    "berlin",
    "paris",
    "singapore",
    "toronto",
]


def score_lead(company_row: dict, role: str, confidence: int = 90) -> int:
    """
    Score a lead from 0-10 based on fit signals. Only contacts >= MIN_LEAD_SCORE get emailed.
    """
    score = 0
    why = (company_row.get("Why Target", "") or "").lower()
    tier = (company_row.get("Tier", "") or "").lower()
    hq = (company_row.get("HQ", "") or "").lower()
    role_lower = role.lower()

    # +3 if company has AI/LLM relevance
    if any(kw in why for kw in AI_KEYWORDS):
        score += 3

    # +2 if role is recruiter or engineering manager
    from contact_finder import RECRUITER_KEYWORDS, EM_KEYWORDS

    if any(k in role_lower for k in RECRUITER_KEYWORDS):
        score += 2
    elif any(k in role_lower for k in EM_KEYWORDS):
        score += 2
    elif any(k in role_lower for k in ["director", "vp", "head of"]):
        score += 1

    # -2 if role is clearly irrelevant
    irrelevant = [
        "sustainability",
        "communications",
        "go-to-market",
        "gtm",
        "real estate",
        "credit risk",
        "creative director",
        "trust and safety",
    ]
    if any(k in role_lower for k in irrelevant):
        score -= 2

    # +1 if Tier 1
    if "tier 1" in tier:
        score += 1

    # +1 if HQ is in a target city
    if any(city in hq for city in TARGET_CITIES):
        score += 1

    # +1 if email confidence is high
    if confidence >= 90:
        score += 1

    # +1 base — every valid contact deserves a small boost
    score += 1

    return max(0, min(10, score))


# ── Combined email generation (1 Gemini call instead of 2) ───────────────────
def generate_outreach_email(
    first_name: str, role: str, company: str, why_target: str
) -> dict:
    """
    Generate subject + body + personalization_reason + quality_score in a single Gemini call.
    Returns dict with keys: subject, body, personalization_reason, quality_score.
    """
    is_em = any(
        x in role.lower()
        for x in ["manager", "lead", "director", "head", "vp", "principal", "cto"]
    )
    email_type = "engineering_manager" if is_em else "recruiter"

    prompt = f"""You are writing a cold job-search email for {YOUR_NAME}.

Goal: Maximize genuine replies, not clicks or volume.

Sender proof:
- Junior AI Engineer who has shipped production LLM, RAG, agentic AI, speech, and document intelligence systems
- AI Engineer, Relay Human Cloud (11+ months)
- Built agentic AI/RAG systems on Azure AI Foundry, PromptFlow, FastAPI
- Reduced LLM latency 70-80%
- Cut document processing effort 75%
- GATE DA 2025 top 9%

Recipient:
Name: {first_name}
Title: {role}
Company: {company}
Company signal: {why_target}

Email type: {email_type}

Rules:
- 70-95 words
- Format the email body using proper HTML tags (<p>, <br>, <ul>, <li>, <strong>) to make it highly readable and visually appealing.
- Break the text into 2-3 very short paragraphs instead of one large block.
- First line must be specific to company/contact.
- Include only one metric as proof, optionally formatted as a short bullet point.
- No exaggerated praise or filler ("I hope you're doing well", "production-grade").
- No attachment mention.
- One soft CTA.
- If recruiter: may ask about open AI/LLM roles.
- If engineering manager: ask for advice on growing as a junior AI engineer, do NOT ask for a job or explicitly say you're not asking for a job.
- Do not list more than 2 technologies.
- Sound like a junior engineer who has shipped real systems, not a marketer.
- Sign off with just the first name: Akshit
- Include a link to the resume below the sign-off formatted as HTML: <br><br><strong>Links:</strong><br><a href="{YOUR_LINKEDIN}">LinkedIn</a> | <a href="{YOUR_RESUME}">Resume</a>

Return valid JSON only. No markdown, no backticks:
{{"subject": "...", "body": "...", "personalization_reason": "...", "quality_score": 1-10}}
"""
    raw = gemini(prompt)
    try:
        data = extract_json(raw)
        # Validate required fields
        for key in ("subject", "body", "quality_score"):
            if key not in data:
                raise KeyError(f"Missing key: {key}")
        data["quality_score"] = int(data.get("quality_score", 5))
        data.setdefault("personalization_reason", "")
        return data
    except Exception as e:
        log.warning(f"Failed to parse JSON for outreach email. Error: {e}")
        clean_raw = raw.replace("```json", "").replace("```", "").strip()
        return {
            "subject": f"AI Engineer — interested in {company}",
            "body": clean_raw,
            "personalization_reason": "",
            "quality_score": 5,
        }


# ── Follow-up (hardcoded template, zero Gemini calls) ────────────────────────
def generate_followup(first_name: str) -> str:
    """Return a lightweight follow-up formatted as HTML. No Gemini call needed."""
    return (
        f"<p>Hi {first_name},</p>"
        "<p>Just following up in case my note got buried.</p>"
        "<p>The short version: I've worked on RAG, agentic AI, PromptFlow, and FastAPI systems, "
        "and I'd be grateful for either a pointer to the right hiring contact or one piece of advice.</p>"
        f"<p>Thanks,<br>{YOUR_NAME}</p>"
        f"<br><strong>Links:</strong><br><a href='{YOUR_LINKEDIN}'>LinkedIn</a> | <a href='{YOUR_RESUME}'>Resume</a>"
    )


# ── Reply classifier ─────────────────────────────────────────────────────────
def classify_reply(reply_body: str, sender_name: str, company: str) -> dict:
    """
    Classify a reply into a category and generate a suggested response.
    Does NOT auto-send — the response is saved to the sheet for manual review.
    """
    prompt = f"""You are classifying a reply to a cold job-search email.

The original email was sent by {YOUR_NAME} (AI Engineer) to {sender_name} at {company}.

Reply text:
---
{reply_body[:1000]}
---

Classify this reply into exactly ONE of these categories:
- positive_interest: wants to talk further, open to connecting
- asks_resume: requesting resume, portfolio, or GitHub
- apply_online: redirects to careers page or application portal
- rejection: not interested, no openings, not hiring
- referral: points to someone else or another team
- out_of_office: auto-reply, vacation, OOO
- do_not_contact: asks to stop emailing, unsubscribe

Then write a SHORT suggested response (2-3 sentences max, warm and professional).
If the category is "out_of_office" or "do_not_contact", set suggested_response to empty string.

Return valid JSON only. No markdown, no backticks:
{{"reply_type": "...", "suggested_response": "..."}}
"""
    raw = gemini(prompt)
    try:
        data = extract_json(raw)
        return {
            "reply_type": data.get("reply_type", "positive_interest"),
            "suggested_response": data.get("suggested_response", ""),
        }
    except Exception as e:
        log.warning(f"Failed to parse reply classification. Error: {e}")
        return {"reply_type": "positive_interest", "suggested_response": ""}


# ── Main daily job ────────────────────────────────────────────────────────────
class OutreachAgent:
    def __init__(self):
        self.sheets = SheetManager(SPREADSHEET_ID)
        self.mailer = GmailSender()

    def run_daily(self):
        today = date.today().isoformat()
        daily_limit = get_daily_limit()
        log.info(f"=== Daily run: {today} | Send limit: {daily_limit} ===")

        # AUTO 0: Check backlog before generating new leads
        companies = self.sheets.get_company_rows()
        backlog = [
            c for c in companies 
            if not c.get("People Found", "").strip() and c.get("Company", "").strip()
        ]
        
        if len(backlog) > 20:
            log.info(f"Skipping lead generation. Backlog is full ({len(backlog)} companies need contacts).")
        else:
            self._generate_leads()

        # AUTO 1: Sync replies and classify them
        self._sync_replies()

        # AUTO 2: Find contacts for companies that have no contacts yet
        self._find_contacts()

        # Phase 1: Send follow-ups for contacts who haven't replied after N days
        self._send_followups(today)

        # Phase 2: Send new outreach emails for today
        self._send_new_outreach(today, daily_limit)

        log.info("=== Daily run complete ===")

    def _sync_replies(self):
        """
        AUTO 1: Check Gmail inbox for replies, classify them, and update the sheet.
        Generates suggested responses but does NOT auto-send them.
        """
        log.info("Syncing replies from Gmail inbox...")
        try:
            outreach_rows = self.sheets.get_outreach_rows()
            known_emails = {r["Email"].lower() for r in outreach_rows if r.get("Email")}
            replied_list = self.mailer.check_replies(known_emails=known_emails)
            synced = 0

            for reply_data in replied_list:
                email = reply_data["email"]
                body = reply_data["body"]
                self.sheets.update_replied(email)
                synced += 1

                # Find company name for this contact
                company = ""
                sender_name = ""
                for r in outreach_rows:
                    if r["Email"].lower() == email:
                        company = r.get("Company", "")
                        sender_name = f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip()
                        break

                # Classify the reply and save suggested response
                if body and body != "(Could not extract reply body)":
                    try:
                        classification = classify_reply(body, sender_name, company)
                        self.sheets.update_reply_classification(
                            email,
                            classification["reply_type"],
                            classification["suggested_response"],
                        )
                        log.info(
                            f"Reply from {email} classified as: {classification['reply_type']}"
                        )
                    except QuotaExhaustedError:
                        log.warning(
                            "Gemini quota exhausted during reply classification. Skipping classification."
                        )
                    except Exception as e:
                        log.warning(f"Reply classification failed for {email}: {e}")

            log.info(f"Reply sync complete — {synced} new replies marked in sheet.")
        except Exception as e:
            log.warning(f"Reply sync failed (non-critical, continuing): {e}")

    def _generate_leads(self):
        """AUTO 0: Discover new startups and add them to the Company Tracker."""
        log.info("Running lead generation...")
        try:
            lead_agent = LeadGeneratorAgent()
            lead_agent.run_daily_generation()
        except Exception as e:
            log.warning(f"Lead generation failed (non-critical, continuing): {e}")

    def _find_contacts(self):
        """AUTO 2: Discover contacts for companies with no contacts in column J."""
        log.info("Running contact discovery...")
        try:
            finder = ContactFinder(
                sheet_manager=self.sheets,
                max_searches=HUNTER_MAX_SEARCHES,
            )
            populated = finder.run()
            log.info(f"Contact discovery done — {populated} companies populated.")
        except Exception as e:
            log.warning(f"Contact discovery failed (non-critical, continuing): {e}")

    def _send_followups(self, today: str):
        contacts = self.sheets.get_outreach_rows()
        sent = 0
        quota_hits = 0
        for row in contacts:
            if (
                row.get("Status") == "Sent"
                and row.get("Replied?", "").lower() != "yes"
                and row.get("Follow-up Sent?", "").lower() != "yes"
                and row.get("Do Not Contact", "").lower() != "yes"
                and row.get("Date Sent")
            ):

                try:
                    sent_date = date.fromisoformat(str(row["Date Sent"]))
                    days_since = (date.today() - sent_date).days
                    if days_since >= FOLLOW_UP_DAYS:
                        # Zero Gemini calls — hardcoded template
                        body = generate_followup(row["First Name"])
                        subject = f"Re: {row.get('Subject', 'Following up')}"
                        self.mailer.send(row["Email"], subject, body)
                        self.sheets.update_followup_sent(row["_row_index"], today)
                        log.info(
                            f"Follow-up sent -> {row['First Name']} {row['Last Name']} @ {row['Company']}"
                        )
                        sent += 1
                        quota_hits = 0
                        time.sleep(2)
                except QuotaExhaustedError as e:
                    quota_hits += 1
                    log.warning(
                        f"Quota exhausted while sending followups. Strike {quota_hits}/3."
                    )
                    if quota_hits >= 3:
                        log.error(
                            "Quota exhausted 3 times. Aborting follow-ups for today."
                        )
                        return
                except Exception as e:
                    err_str = str(e).lower()
                    if (
                        "quota" in err_str
                        or "rate limit" in err_str
                        or "429" in err_str
                    ):
                        quota_hits += 1
                        log.warning(
                            f"Quota exhausted (Mail/Other). Strike {quota_hits}/3."
                        )
                        if quota_hits >= 3:
                            log.error(
                                "Quota exhausted 3 times. Aborting follow-ups for today."
                            )
                            return
                    log.error(f"Follow-up failed for row {row.get('_row_index')}: {e}")
        log.info(f"Follow-ups sent: {sent}")

    def _send_new_outreach(self, today: str, daily_limit: int):
        companies = self.sheets.get_company_rows()
        outreach_rows = self.sheets.get_outreach_rows()
        already_contacted = {
            r["Email"].lower() for r in outreach_rows if r.get("Email")
        }

        # Build Do Not Contact set
        do_not_contact = {
            r["Email"].lower()
            for r in outreach_rows
            if r.get("Do Not Contact", "").lower() == "yes"
        }

        sent_today = 0
        quota_hits = 0

        for company_row in companies:
            if sent_today >= daily_limit:
                break

            company = company_row.get("Company", "")
            if not company:
                continue

            contacts_raw = company_row.get("People Found", "").strip()
            if not contacts_raw or contacts_raw in ("0", "0.0"):
                continue

            # Parse contacts (format: "Name|email|role|confidence\n...")
            for contact_line in str(contacts_raw).strip().split("\n"):
                if sent_today >= daily_limit:
                    break
                parts = contact_line.split("|")
                if len(parts) < 3:
                    continue

                full_name = parts[0].strip()
                email = parts[1].strip()
                role = parts[2].strip()
                confidence = int(parts[3].strip()) if len(parts) > 3 else 90
                first_name = full_name.split()[0] if full_name else "there"

                # Skip already contacted
                if email.lower() in already_contacted:
                    continue

                # Skip Do Not Contact
                if email.lower() in do_not_contact:
                    log.info(f"Skipping {email} — marked Do Not Contact")
                    continue

                # ── Lead scoring gate ──
                lead_score = score_lead(company_row, role, confidence)
                if lead_score < MIN_LEAD_SCORE:
                    log.info(
                        f"Skipping {full_name} @ {company} — lead score {lead_score} < {MIN_LEAD_SCORE}"
                    )
                    continue

                try:
                    # Single Gemini call for hook + subject + body
                    email_data = generate_outreach_email(
                        first_name, role, company, company_row.get("Why Target", "")
                    )

                    subject = email_data["subject"]
                    body = email_data["body"]
                    quality_score = email_data["quality_score"]
                    hook = email_data.get("personalization_reason", "")

                    # ── Quality gate ──
                    if quality_score < 5:
                        log.info(
                            f"Skipping {full_name} @ {company} — Gemini quality_score {quality_score} < 5"
                        )
                        continue

                    self.mailer.send(email, subject, body)

                    self.sheets.append_outreach_row(
                        {
                            "First Name": first_name,
                            "Last Name": (
                                " ".join(full_name.split()[1:])
                                if len(full_name.split()) > 1
                                else ""
                            ),
                            "Company": company,
                            "Role / Title": role,
                            "Email": email,
                            "Hook": hook,
                            "Subject": subject,
                            "Status": "Sent",
                            "Date Sent": today,
                            "Opened?": "No",
                            "Replied?": "No",
                            "Follow-up Sent?": "No",
                            "Outcome": "Awaiting reply",
                            "Notes": f"Auto-sent on {today} | Score: {lead_score} | Quality: {quality_score}",
                            "Lead Score": str(lead_score),
                        }
                    )

                    self.sheets.update_company_emails_sent(company_row["_row_index"])
                    already_contacted.add(email.lower())
                    log.info(
                        f"Sent -> {first_name} @ {company} ({role}) [score={lead_score}, quality={quality_score}]"
                    )
                    sent_today += 1
                    quota_hits = 0
                    time.sleep(3)

                except QuotaExhaustedError as e:
                    quota_hits += 1
                    log.warning(
                        f"Quota exhausted while sending outreach. Strike {quota_hits}/3."
                    )
                    if quota_hits >= 3:
                        log.error(
                            "Quota exhausted 3 times. Aborting new outreach for today."
                        )
                        return
                except Exception as e:
                    err_str = str(e).lower()
                    if (
                        "quota" in err_str
                        or "rate limit" in err_str
                        or "429" in err_str
                    ):
                        quota_hits += 1
                        log.warning(
                            f"Quota exhausted (Mail/Other). Strike {quota_hits}/3."
                        )
                        if quota_hits >= 3:
                            log.error(
                                "Quota exhausted 3 times. Aborting new outreach for today."
                            )
                            return
                    log.error(f"Failed to send to {full_name} @ {company}: {e}")

        log.info(f"New emails sent today: {sent_today}")


if __name__ == "__main__":
    agent = OutreachAgent()
    agent.run_daily()
