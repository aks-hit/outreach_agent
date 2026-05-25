"""
lead_generator.py — Autonomous Lead Generation Agent
Runs daily or on-demand: discovers tech startups, consulting firms, PM programs,
and VC-backed companies in target locations. Tailors "Why Target" pitches based
on the user's profile.txt and appends them to the "Company Tracker" Google Sheet.
"""

from dotenv import load_dotenv

load_dotenv()

import os
import sys
import time
import json
import logging
from datetime import datetime
from google import genai
from sheets import SheetManager


class QuotaExhaustedError(Exception):
    pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("lead_generator.log"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
YOUR_NAME = os.environ.get("YOUR_NAME", "[Your Name]")
YOUR_COLLEGE = os.environ.get("YOUR_COLLEGE", "IIM Kashipur")  # Your own MBA college

# Top 20 MBA colleges in India for alumni outreach
TOP_MBA_COLLEGES_INDIA = [
    "IIM Ahmedabad",
    "IIM Bangalore",
    "IIM Calcutta",
    "IIM Lucknow",
    "IIM Kozhikode",
    "IIM Indore",
    "IIM Shillong",
    "IIM Rohtak",
    "IIM Raipur",
    "IIM Ranchi",
    "IIM Udaipur",
    "IIM Trichy",
    "IIM Kashipur",
    "IIM Visakhapatnam",
    "IIM Nagpur",
    "XLRI Jamshedpur",
    "FMS Delhi",
    "MDI Gurgaon",
    "SPJIMR Mumbai",
    "ISB Hyderabad",
]

# Read profile summary from file
profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.txt")
try:
    with open(profile_path, "r", encoding="utf-8") as f:
        PROFILE_SUMMARY = f.read().strip()
except FileNotFoundError:
    log.warning(f"{profile_path} not found! Please create it from profile.txt.example.")
    PROFILE_SUMMARY = "Software Engineer looking for roles."

# Default target hubs queue
# Order = Priority: Own alumni first, then top MBA peers, then cities, then domain-specific
DEFAULT_QUEUE = [
    # ── Priority 1: Your own college alumni ──
    # These targets are generated dynamically from YOUR_COLLEGE env var at runtime.
    # Placeholder — replaced by _build_alumni_queue() at startup.
    {"type": "alumni_own", "name": YOUR_COLLEGE, "domain": "PM & Tech Consulting & AI"},

    # ── Priority 2: Top MBA college alumni in the same domain ──
    {"type": "alumni_peer", "name": "IIM Ahmedabad",      "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "IIM Bangalore",      "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "IIM Calcutta",       "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "IIM Lucknow",        "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "ISB Hyderabad",      "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "XLRI Jamshedpur",    "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "FMS Delhi",          "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "MDI Gurgaon",        "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "SPJIMR Mumbai",      "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "IIM Kozhikode",      "domain": "PM & Tech Consulting"},
    {"type": "alumni_peer", "name": "IIM Indore",         "domain": "PM & Tech Consulting"},

    # ── Priority 3: Indian Tech Hubs ──
    {"type": "city", "name": "Bengaluru",  "country": "India"},
    {"type": "city", "name": "Hyderabad",  "country": "India"},
    {"type": "city", "name": "Gurgaon",    "country": "India"},
    {"type": "city", "name": "Mumbai",     "country": "India"},
    {"type": "city", "name": "Pune",       "country": "India"},

    # ── Priority 4: Management Consulting Firms ──
    {"type": "consulting", "name": "MBB + Big4 Tech Consulting"},
    {"type": "consulting", "name": "Boutique Tech Strategy Firms India"},

    # ── Priority 5: APM / PM Programs ──
    {"type": "pm_programs", "name": "APM Programs India"},
    {"type": "pm_programs", "name": "PM Programs Global Tech"},

    # ── Priority 6: VC-Backed Startups ──
    {"type": "vc_backed", "name": "Series A-B AI Startups India 2024"},
    {"type": "vc_backed", "name": "Series A-B SaaS Startups India 2024"},

    # ── Priority 7: Global Tech Hubs ──
    {"type": "city", "name": "San Francisco / Silicon Valley", "country": "USA"},
    {"type": "city", "name": "Singapore", "country": "Singapore"},
]

# ── Gemini setup ──────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)


def gemini(prompt: str, retries=4) -> str:
    """Call Gemini with rate-limiting and exponential backoff retry."""
    # Mandatory sleep to respect the 15 RPM (Requests Per Minute) free tier limit
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
                    log.warning(
                        f"Gemini rate limit hit in LeadGen. Retrying in {sleep_time}s... Error: {e}"
                    )
                    time.sleep(sleep_time)
                else:
                    sleep_time = 5 * (attempt + 1)
                    log.warning(
                        f"Gemini error in LeadGen. Retrying in {sleep_time}s... Error: {e}"
                    )
                    time.sleep(sleep_time)
            else:
                log.error(
                    f"Gemini call failed permanently in LeadGen after {retries} attempts: {e}"
                )
                if is_rate_limit:
                    raise QuotaExhaustedError(f"Gemini Rate Limit Exhausted: {e}")
                raise
    return ""


class LeadGeneratorAgent:
    def __init__(self):
        self.sheets = SheetManager(SPREADSHEET_ID)
        self.state_file = "lead_generator_state.json"
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Failed to read state file: {e}. Starting fresh.")

        # Initial fresh state
        return {
            "queue": DEFAULT_QUEUE,
            "current_index": 0,
            "completed": [],
            "last_run": None,
        }

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            log.error(f"Failed to save state file: {e}")

    def run_daily_generation(self):
        log.info("=== Autonomous Lead Generation Run Started ===")

        queue = self.state.get("queue", DEFAULT_QUEUE)
        idx = self.state.get("current_index", 0)

        if idx >= len(queue):
            log.info(
                "Finished all target regions in the queue! Wrapping around to restart queue."
            )
            idx = 0
            self.state["current_index"] = 0

        target = queue[idx]
        log.info(f"Targeting: {target['type'].upper()} -> {target['name']}")

        # Step 1: Query Gemini to discover notable tech/AI companies in this target
        companies = self._discover_companies(target)
        log.info(f"Gemini suggested {len(companies)} companies in this category.")

        if not companies:
            log.warning(
                "No companies retrieved. Moving to the next target in the queue."
            )
            self.state["current_index"] = idx + 1
            self._save_state()
            return

        # Step 2: Read existing companies in Google Sheet to avoid duplicates
        existing_companies = self.sheets.get_company_rows()
        existing_names = {
            c["Company"].lower().strip() for c in existing_companies if c.get("Company")
        }

        # Step 3: Filter and Append new companies
        added_count = 0
        for company_data in companies:
            name = company_data.get("company", "").strip()
            if not name:
                continue

            if name.lower().strip() in existing_names:
                log.info(
                    f"Company '{name}' already exists in the sheets tracker. Skipping."
                )
                continue

            try:
                # Format: '#' (we use empty/autoincremented or write_sheet updates it), Company, Tier, HQ, Why Target, Priority, Status, People Found, Emails Sent
                # Sheets COL_COMPANY_PEOPLE (Col J) must be set to '0' (placeholder) for the contact finder to process it.
                # Col K (Emails Sent) starts at 0.
                # Sheet row format in SheetManager.get_company_rows maps columns:
                # Row data structure in Google Sheets starts at A5: A is Number, B is Company, C is Tier, D is HQ, E is Why Target, etc.
                # Let's append directly to the sheet range A:K.
                # Columns mapping:
                # B: Company, C: Tier, D: HQ, E: Why Target, F: Priority (e.g. Medium), G: Status (e.g. Dream), H: '0' (People Found), I: 0 (Emails Sent)

                # Wait, SheetManager has no append_company_row method, it only has update_people_found and get_company_rows.
                # Let's check how Sheets B:K is structured.
                # Let's check the columns using read_sheet logs.
                # In read_sheet logs, we saw:
                # Row 5: Company: Google, People Found: '0', Why Target: Top AI infrastructure and research.
                # Let's write a direct sheet append/write helper in lead_generator or add it.
                # Let's write a simple helper inside lead_generator to write to company sheet!
                self._append_company_to_sheet(company_data)
                log.info(f"Successfully added '{name}' to sheet tracker.")
                added_count += 1
            except Exception as e:
                log.error(f"Failed to append company '{name}' to sheet: {e}")

        # Step 4: Advance the queue
        self.state["current_index"] = idx + 1
        self.state["completed"].append(target)
        self.state["last_run"] = datetime.now().date().isoformat()
        self._save_state()

        log.info(
            f"=== Autonomous Lead Generation Complete: Added {added_count} new startups ==="
        )

    def _discover_companies(self, target: dict) -> list[dict]:
        """Ask Gemini for 10-15 real active companies in target."""
        profile = PROFILE_SUMMARY

        if target["type"] in ("alumni_own", "alumni_peer"):
            college = target["name"]
            domain = target.get("domain", "Product Management & Tech Consulting")
            is_own = target["type"] == "alumni_own"
            priority_note = (
                "This is the candidate's OWN college — these alumni are the highest priority contacts. "
                "Emphasize the shared alumni connection warmly."
                if is_own
                else f"This is a peer institution. Connect via the shared MBA background."
            )
            prompt = f"""
You are a career advisor helping an MBA student do warm alumni outreach.

{priority_note}

Task: List 12 real professionals who:
1. Are alumni of "{college}"
2. Currently work in: {domain}
3. Hold roles like: Product Manager, Associate PM, Tech Consultant, Strategy Consultant, Business Analyst, Engagement Manager, or similar.
4. Work at well-known companies (tech, consulting, startups, MNCs).

For each person provide:
- "company": The company they currently work at.
- "tier": "Tier 1" (top company like McKinsey, Google, Flipkart) or "Tier 2" (mid-size/startup).
- "hq": The city/country of the company HQ.
- "why_target": ONE sentence about why this specific alumni at this company is worth reaching out to, given this candidate profile:
{profile}

Return ONLY a raw JSON array. No markdown, no backticks:
[
  {{"company": "CompanyName", "tier": "Tier 1", "hq": "City, Country", "why_target": "Specific reason...", "alumni_college": "{college}"}}
]
"""

        elif target["type"] == "city":
            prompt = f"""
Discover 12 real, active tech companies, startups, and product-led companies in {target['name']}, {target['country']}.
Return a raw JSON array. Do not include markdown code block formatting or backticks.

For each company, provide:
1. "company": The official name of the company.
2. "tier": Either "Tier 1" (well-known/fast-growing) or "Tier 2" (promising mid-sized/startup).
3. "hq": Exactly "{target['name']}, {target['country']}".
4. "why_target": ONE sentence explaining why a candidate with THIS profile should target them — be specific to their product/mission:
{profile}

Format:
[
  {{"company": "ExampleName", "tier": "Tier 1", "hq": "{target['name']}, {target['country']}", "why_target": "Specific reason based on their product and the candidate's profile..."}}
]
"""

        elif target["type"] == "yc_batch":
            prompt = f"""
Discover 12 real, active startups funded in Y Combinator batch '{target['name']}'.
Return a raw JSON array. Do not include markdown code block formatting or backticks.

For each startup, provide:
1. "company": The official name of the company.
2. "tier": "Tier 1" or "Tier 2".
3. "hq": The actual HQ location (e.g. San Francisco, USA).
4. "why_target": ONE sentence explaining why a candidate with THIS profile should target them:
{profile}

Format:
[
  {{"company": "ExampleName", "tier": "Tier 1", "hq": "HQ Location", "why_target": "Specific reason based on their product and the candidate's profile..."}}
]
"""

        elif target["type"] == "consulting":
            prompt = f"""
List 12 real management consulting or tech strategy firms matching this category: '{target['name']}'.
Include both MBB (McKinsey, BCG, Bain), Big4 tech arms (Deloitte Digital, Accenture, EY, KPMG),
and boutique strategy/product consulting firms.
Return a raw JSON array. Do not include markdown code block formatting or backticks.

For each firm, provide:
1. "company": The official name.
2. "tier": "Tier 1" (MBB/Big4) or "Tier 2" (boutique).
3. "hq": The primary HQ location.
4. "why_target": ONE sentence explaining why a candidate with THIS profile should target them — mention their specific practice areas:
{profile}

Format:
[
  {{"company": "ExampleName", "tier": "Tier 1", "hq": "HQ Location", "why_target": "Specific reason..."}}
]
"""

        elif target["type"] == "pm_programs":
            prompt = f"""
List 12 real companies that have formal Associate Product Manager (APM) or Product Manager programs
matching this category: '{target['name']}'.
Include both large tech (Google, Microsoft, Amazon, Flipkart, Razorpay, Swiggy, Zepto)
and growth-stage startups with structured PM tracks.
Return a raw JSON array. Do not include markdown code block formatting or backticks.

For each company, provide:
1. "company": The official name.
2. "tier": "Tier 1" (large tech) or "Tier 2" (growth startup).
3. "hq": The primary HQ location.
4. "why_target": ONE sentence explaining why a candidate with THIS profile is a strong PM fit for this company:
{profile}

Format:
[
  {{"company": "ExampleName", "tier": "Tier 1", "hq": "HQ Location", "why_target": "Specific reason..."}}
]
"""

        elif target["type"] == "vc_backed":
            prompt = f"""
List 12 real VC-backed startups matching this category: '{target['name']}'.
Focus on companies that have raised Series A or Series B in the last 2 years and are actively hiring.
Return a raw JSON array. Do not include markdown code block formatting or backticks.

For each startup, provide:
1. "company": The official name.
2. "tier": "Tier 1" (well-known/fast-growing) or "Tier 2" (promising early-stage).
3. "hq": The primary HQ location.
4. "why_target": ONE sentence explaining why a candidate with THIS profile should target them:
{profile}

Format:
[
  {{"company": "ExampleName", "tier": "Tier 1", "hq": "HQ Location", "why_target": "Specific reason..."}}
]
"""

        else:
            log.warning(f"Unknown target type: {target['type']}. Skipping.")
            return []

        raw = gemini(prompt)
        # Strip markdown wrappers if any
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            return json.loads(raw)
        except Exception as e:
            log.error(f"Failed to parse Gemini response as JSON: {e}")
            log.debug(f"Raw response was: {raw}")
            return []

    def _append_company_to_sheet(self, c: dict):
        """Append a new company row to 'Company Tracker'."""
        # Get the next index number
        existing = self.sheets.get_company_rows()
        next_num = len(existing) + 1

        # B: Company, C: Tier, D: HQ, E: Why Target, H: Priority, I: Status, J: People Found ('0'), K: Emails Sent (0)
        # In sheets.py, get_company_rows reads columns B to K:
        # COL_COMPANY_NAME       = 2 (B)
        # COL_COMPANY_TIER       = 3 (C)
        # COL_COMPANY_HQ         = 4 (D)
        # COL_COMPANY_WHY        = 5 (E)
        # COL_COMPANY_PRIORITY   = 8 (H)
        # COL_COMPANY_STATUS     = 9 (I)
        # COL_COMPANY_PEOPLE     = 10 (J)
        # COL_COMPANY_EMAILS_SENT= 11 (K)

        # Assemble values array matching columns A to K:
        row = [
            next_num,  # Col A (#)
            c.get("company", "").strip(),  # Col B (Company)
            c.get("tier", "Tier 2").strip(),  # Col C (Tier)
            c.get("hq", "Unknown").strip(),  # Col D (HQ)
            c.get("why_target", "").strip(),  # Col E (Why Target)
            "",  # Col F (placeholder)
            "",  # Col G (placeholder)
            "Medium",  # Col H (Priority)
            "Dream",  # Col I (Status)
            "0",  # Col J (People Found - '0' is crucial!)
            "0",  # Col K (Emails Sent)
        ]

        # Append to Google Sheet 'Company Tracker'
        self.sheets._append("'Company Tracker'!A:K", [row])


if __name__ == "__main__":
    agent = LeadGeneratorAgent()
    agent.run_daily_generation()
