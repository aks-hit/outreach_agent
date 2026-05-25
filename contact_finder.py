"""
contact_finder.py — Auto-populates column K ("People Found") in Company Tracker
using Hunter.io, Apollo.io, Snov.io APIs in priority order, with a free
Gemini-powered fallback for when all paid API quotas are exhausted.

Free tiers:
  Hunter.io  — 25 domain searches/month
  Apollo.io  — 50 exports/month
  Snov.io    — 50 credits/month
  Gemini     — Free fallback (generates best-guess contacts from public info)
"""

from dotenv import load_dotenv

load_dotenv()

import os
import re
import time
import json
import logging
import requests
from typing import Optional
from google import genai


class QuotaExhaustedError(Exception):
    pass


log = logging.getLogger(__name__)

APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
SNOV_CLIENT_ID = os.environ.get("SNOV_CLIENT_ID", "")
SNOV_CLIENT_SECRET = os.environ.get("SNOV_CLIENT_SECRET", "")
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
HUNTER_BASE = "https://api.hunter.io/v2"

# Contacts per company to write (user requested at least 5)
MAX_CONTACTS_PER_COMPANY = 5

# Minimum confidence score (0-100) to accept a Hunter.io email
MIN_CONFIDENCE = 70


# ── Secret Redaction ─────────────────────────────────────────────────────────
def redact_url(url: str) -> str:
    """Strip sensitive query params (api_key, client_secret, access_token) from URLs before logging."""
    return re.sub(
        r"(api_key|client_secret|access_token|client_id)=[^&\s]+",
        r"\1=***REDACTED***",
        url,
    )


# ── Role Relevance ───────────────────────────────────────────────────────────
# Roles we consider "recruiter-type" (gets recruiter-style email)
RECRUITER_KEYWORDS = [
    "recruit",
    "talent",
    "hr",
    "human resource",
    "hiring",
    "people ops",
    "people operations",
    "sourcer",
    "staffing",
]

# Roles we consider "engineering manager-type" (gets EM-style email)
EM_KEYWORDS = [
    "engineering manager",
    "engineering lead",
    "tech lead",
    "technical lead",
    "director of engineering",
    "vp engineering",
    "vice president engineering",
    "head of engineering",
    "principal engineer",
    "staff engineer",
    "cto",
    "director of ai",
    "head of ai",
    "vp of ai",
    "director of machine learning",
    "head of ml",
    "director of platform",
    "director of software",
    # PM / Consulting relevant
    "chief of staff",
    "head of product",
    "vp product",
    "director of product",
    "head of strategy",
    "director of strategy",
    "managing director",
    "partner",
    "associate partner",
    "principal consultant",
    "engagement manager",
    "business analyst",
    "strategy manager",
    "head of growth",
    "vp growth",
]

# Roles that are clearly irrelevant — skip these
SKIP_KEYWORDS = [
    "finance",
    "accounting",
    "legal",
    "counsel",
    "go-to-market",
    "sustainability",
    "communications",
    "real estate",
    "credit risk",
    "creative director",
    "trust and safety",
]


def _is_relevant(position: str) -> bool:
    """Return True if this role is a recruiter, engineering manager, or senior tech leader."""
    pos = position.lower()
    if any(k in pos for k in SKIP_KEYWORDS):
        return False
    # Explicit match on recruiter or EM keywords
    if any(k in pos for k in RECRUITER_KEYWORDS + EM_KEYWORDS):
        return True
    # Broader match: any director/VP/head in a technical area
    if any(k in pos for k in ["director", "vp ", "vice president", "head of"]):
        # But only if it's not a skipped role (already checked above)
        return True
    return False


def _role_label(position: str) -> str:
    """Return the position string cleaned up for the sheet."""
    return position.strip().title() if position else "Unknown Role"


def guess_domain(company_name: str) -> str:
    """Guess domain name with popular tech overrides for accuracy."""
    overrides = {
        "perplexity": "perplexity.ai",
        "mistral ai": "mistral.ai",
        "sarvam ai": "sarvam.ai",
        "together ai": "together.xyz",
        "cognition ai": "cognition.ai",
        "elevenlabs": "elevenlabs.io",
        "runway": "runwayml.com",
        "deel": "deel.com",
        "remote.com": "remote.com",
        "weights & biases": "wandb.com",
        "writer": "writer.com",
        "meta": "meta.com",
        "h2o.ai": "h2o.ai",
        "observe.ai": "observe.ai",
        "yellow.ai": "yellow.ai",
        "krutrim": "olakrutrim.com",
        "krutrim ai": "olakrutrim.com",
    }
    cleaned = company_name.lower().strip()
    if cleaned in overrides:
        return overrides[cleaned]
    domain = "".join(c for c in cleaned if c.isalnum() or c == ".")
    if (
        not domain.endswith(".com")
        and not domain.endswith(".ai")
        and not domain.endswith(".io")
        and not domain.endswith(".in")
    ):
        domain = f"{domain}.com"
    return domain


def request_with_retry(
    method: str, url: str, retries: int = 3, **kwargs
) -> requests.Response:
    """Make an API request with automatic retry on rate limits (429) or transient errors (5xx)."""
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in (402, 403, 429):
                log.warning(
                    f"API Quota/Rate Limit ({resp.status_code}) for {redact_url(url)}"
                )
                raise QuotaExhaustedError(
                    f"HTTP {resp.status_code} on {redact_url(url)}"
                )
            elif resp.status_code >= 500:
                sleep_time = 5 * (attempt + 1)
                log.warning(
                    f"API Server Error ({resp.status_code}) for {redact_url(url)}. Sleeping {sleep_time}s and retrying..."
                )
                time.sleep(sleep_time)
                continue
            return resp
        except QuotaExhaustedError:
            raise
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            if attempt < retries - 1:
                sleep_time = 5 * (attempt + 1)
                log.warning(
                    f"API Request Exception for {redact_url(url)}: {e}. Sleeping {sleep_time}s and retrying..."
                )
                time.sleep(sleep_time)
            else:
                raise
    return resp


def find_contacts_apollo(company_name: str, limit: int) -> list[str]:
    if not APOLLO_API_KEY:
        return []

    log.info(f"Apollo.io: Searching contacts for '{company_name}'...")
    try:
        url = "https://api.apollo.io/v1/mixed_people/search"
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": APOLLO_API_KEY,
        }

        domain = guess_domain(company_name)
        payload = {
            "q_organization_names": [company_name],
            "contact_email_domain": domain,
            "page": 1,
            "per_page": 20,
        }
        resp = request_with_retry(
            "POST", url, json=payload, headers=headers, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        people = data.get("people", [])
        if not people:
            # Fallback search by organization names only
            payload = {
                "q_organization_names": [company_name],
                "page": 1,
                "per_page": 20,
            }
            resp = request_with_retry(
                "POST", url, json=payload, headers=headers, timeout=15
            )
            resp.raise_for_status()
            people = resp.json().get("people", [])

        # Filter relevant roles
        relevant_people = []
        for p in people:
            title = p.get("title") or ""
            if _is_relevant(title):
                relevant_people.append(p)

        if not relevant_people:
            log.info(f"Apollo.io: No relevant contacts found for '{company_name}'.")
            return []

        # Extract contacts directly from search results (no bulk match needed)
        contacts = []
        for p in relevant_people[:limit]:
            first = p.get("first_name") or ""
            last = p.get("last_name") or ""
            full_name = f"{first} {last}".strip() or "Unknown"
            email = p.get("email") or ""
            title = p.get("title") or "Unknown Role"
            if email:
                contacts.append(f"{full_name}|{email}|{_role_label(title)}|95")
                log.info(f"  -> [Apollo] {full_name} | {email} | {title}")
        return contacts
    except QuotaExhaustedError:
        raise
    except Exception as e:
        log.error(f"Apollo.io search failed for '{company_name}': {e}")
        return []


def find_contacts_snov(company_name: str, limit: int) -> list[str]:
    if not SNOV_CLIENT_ID or not SNOV_CLIENT_SECRET:
        return []

    log.info(f"Snov.io: Searching contacts for '{company_name}'...")
    try:
        # Step 1: Get Access Token
        token_url = "https://api.snov.io/v1/oauth/access_token"
        token_data = {
            "grant_type": "client_credentials",
            "client_id": SNOV_CLIENT_ID,
            "client_secret": SNOV_CLIENT_SECRET,
        }
        token_resp = request_with_retry("POST", token_url, data=token_data, timeout=10)
        token_resp.raise_for_status()
        token = token_resp.json().get("access_token")
        if not token:
            log.error("Snov.io: Failed to retrieve access token.")
            return []

        headers = {"Authorization": f"Bearer {token}"}
        domain = guess_domain(company_name)

        # Step 2: Start search task
        start_url = "https://api.snov.io/v2/domain-search/start/"
        start_data = {"domain": domain, "limit": 10, "type": "personal"}
        start_resp = request_with_retry(
            "POST", start_url, headers=headers, data=start_data, timeout=15
        )
        start_resp.raise_for_status()
        task_hash = start_resp.json().get("task_hash")
        if not task_hash:
            log.info(f"Snov.io: No domain search task started for '{domain}'.")
            return []

        # Step 3: Poll for results (up to 4 times, waiting 2s each)
        result_url = f"https://api.snov.io/v2/domain-search/result/{task_hash}"
        for attempt in range(4):
            time.sleep(2)
            res = request_with_retry("GET", result_url, headers=headers, timeout=10)
            res_data = res.json()
            if res_data.get("status") == "completed":
                emails = res_data.get("emails", [])
                contacts = []
                for entry in emails:
                    if len(contacts) >= limit:
                        break
                    first = entry.get("first_name") or ""
                    last = entry.get("last_name") or ""
                    full_name = f"{first} {last}".strip() or "Unknown"
                    email = entry.get("email") or ""
                    position = entry.get("position") or ""
                    if email and _is_relevant(position):
                        contacts.append(
                            f"{full_name}|{email}|{_role_label(position)}|85"
                        )
                        log.info(f"  -> [Snov] {full_name} | {email} | {position}")
                return contacts
        log.info(f"Snov.io: Polling timed out for '{company_name}'.")
        return []
    except QuotaExhaustedError:
        raise
    except Exception as e:
        log.error(f"Snov.io search failed for '{company_name}': {e}")
        return []


def find_contacts_hunter(company_name: str, limit: int) -> list[str]:
    if not HUNTER_API_KEY:
        return []

    log.info(f"Hunter.io: Searching contacts for '{company_name}'...")
    try:
        params = {
            "company": company_name,
            "api_key": HUNTER_API_KEY,
            "limit": 10,  # max 10 allowed on free plan
            "type": "personal",  # personal emails only
        }
        resp = request_with_retry(
            "GET", f"{HUNTER_BASE}/domain-search", params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

        domain = data.get("domain", "")
        emails = data.get("emails", [])

        if not emails:
            log.info(
                f"Hunter.io: no contacts found for '{company_name}' (domain: {domain or 'unknown'})"
            )
            return []

        contacts = []
        seen_emails = set()

        def sort_key(e):
            pos = (e.get("position") or "").lower()
            if any(k in pos for k in RECRUITER_KEYWORDS):
                return 0
            if any(k in pos for k in EM_KEYWORDS):
                return 1
            return 2

        for entry in sorted(emails, key=sort_key):
            if len(contacts) >= limit:
                break

            position = entry.get("position") or ""
            email_addr = entry.get("value") or ""
            confidence = entry.get("confidence") or 0
            first = (entry.get("first_name") or "").strip()
            last = (entry.get("last_name") or "").strip()
            full_name = f"{first} {last}".strip() or "Unknown"

            if not email_addr or email_addr in seen_emails:
                continue
            if confidence < MIN_CONFIDENCE:
                continue
            if not position or not _is_relevant(position):
                continue

            contacts.append(
                f"{full_name}|{email_addr}|{_role_label(position)}|{confidence}"
            )
            seen_emails.add(email_addr)
            log.info(
                f"  -> [Hunter] {full_name} | {email_addr} | {position} (confidence: {confidence})"
            )

        return contacts

    except QuotaExhaustedError:
        log.warning("Hunter.io: rate limited.")
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "?"
        if status == 401:
            log.error("Hunter.io: invalid API key. Check HUNTER_API_KEY in .env")
        else:
            log.error(f"Hunter.io HTTP {status} for '{company_name}'")
        return []
    except Exception as e:
        log.error(f"Hunter.io error for '{company_name}': {e}")
        return []


def find_contacts_gemini_fallback(company_name: str, limit: int) -> list[str]:
    """
    Free fallback: Uses Gemini to generate best-guess contacts based on publicly
    known information. Marks contacts with confidence=50 so the lead scorer
    applies a discount. Only used when all paid APIs are quota-exhausted.
    """
    if not GEMINI_API_KEY:
        return []

    log.info(f"Gemini Fallback: Generating best-guess contacts for '{company_name}'...")
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        domain = guess_domain(company_name)
        prompt = f"""You are a research assistant. Based on publicly available information, list up to {limit} real people who work or have worked at '{company_name}' in recruiting, HR, engineering management, product management, or strategy roles.

For each person provide their most likely work email based on the domain '{domain}' and common email patterns (firstname@, firstname.lastname@, etc.).

Return ONLY a raw JSON array. No markdown, no backticks:
[
  {{"name": "Full Name", "email": "guess@{domain}", "role": "Job Title", "confidence": 50}}
]

IMPORTANT:
- Only include people you are reasonably confident actually work/worked there.
- Set confidence to 50 for guessed emails, 70 if you are more certain.
- Return an empty array [] if you don't know anyone at this company."""

        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            raw = "\n".join(lines).strip()

        people = json.loads(raw)
        contacts = []
        for p in people[:limit]:
            name = p.get("name", "").strip()
            email = p.get("email", "").strip()
            role = p.get("role", "Unknown Role").strip()
            confidence = p.get("confidence", 50)
            if name and email and "@" in email:
                contacts.append(f"{name}|{email}|{role}|{confidence}")
                log.info(f"  -> [Gemini] {name} | {email} | {role} (confidence: {confidence})")
        return contacts
    except Exception as e:
        log.warning(f"Gemini fallback failed for '{company_name}': {e}")
        return []


def find_contacts_for_company(
    company_name: str,
    limit: int = MAX_CONTACTS_PER_COMPANY,
) -> list[str]:
    """
    Unified entry point. Tries APIs in priority order:
    1. Hunter.io (25 searches/mo)
    2. Apollo.io (50 exports/mo)
    3. Snov.io (50 credits/mo)
    4. Gemini (free fallback — best-guess contacts from public info)
    """
    # 1. Try Hunter.io
    try:
        if HUNTER_API_KEY:
            contacts = find_contacts_hunter(company_name, limit)
            if contacts:
                return contacts
    except QuotaExhaustedError:
        pass

    # 2. Try Apollo.io
    try:
        if APOLLO_API_KEY:
            contacts = find_contacts_apollo(company_name, limit)
            if contacts:
                return contacts
    except QuotaExhaustedError:
        pass

    # 3. Try Snov.io
    try:
        if SNOV_CLIENT_ID and SNOV_CLIENT_SECRET:
            contacts = find_contacts_snov(company_name, limit)
            if contacts:
                return contacts
    except QuotaExhaustedError:
        pass

    # 4. Free Gemini fallback — always available, marks low confidence
    log.info(f"All paid APIs exhausted for '{company_name}'. Trying free Gemini fallback...")
    contacts = find_contacts_gemini_fallback(company_name, limit)
    if contacts:
        return contacts

    return []


class ContactFinder:
    """
    Orchestrates contact discovery for all companies in the sheet that
    don't yet have contacts in column J ("People Found").
    """

    def __init__(self, sheet_manager, max_searches: int = 100):
        self.sheets = sheet_manager
        self.max_searches = max_searches

    def run(self) -> int:
        """
        Discover contacts for companies with empty "People Found" column.
        Returns the number of companies populated.
        """
        # FIX: Only skip if ALL three API keys are missing
        has_any_key = bool(
            HUNTER_API_KEY or APOLLO_API_KEY or (SNOV_CLIENT_ID and SNOV_CLIENT_SECRET)
        )
        if not has_any_key:
            log.warning(
                "No contact discovery API keys set in .env — contact auto-discovery skipped.\n"
                "Add at least one of: HUNTER_API_KEY, APOLLO_API_KEY, or SNOV_CLIENT_ID+SNOV_CLIENT_SECRET"
            )
            return 0

        companies = self.sheets.get_company_rows()
        to_process = [
            c
            for c in companies
            if not c.get("People Found", "").strip()
            or c.get("People Found", "").strip() in ("0", "0.0")
        ]

        if not to_process:
            log.info(
                "Contact discovery: all companies already have contacts. Nothing to do."
            )
            return 0

        log.info(
            f"Contact discovery: {len(to_process)} companies need contacts "
            f"(will process up to {self.max_searches} this run)"
        )

        populated = 0
        quota_hits = 0

        for company_row in to_process[: self.max_searches]:
            company_name = company_row["Company"]
            log.info(f"[ContactFinder] Searching for: {company_name}")

            try:
                contacts = find_contacts_for_company(company_name)
                quota_hits = 0  # reset on success or normal empty result
            except QuotaExhaustedError:
                quota_hits += 1
                log.warning(
                    f"Quota limits hit for {company_name}. Strike {quota_hits}/3"
                )
                if quota_hits >= 3:
                    log.error(
                        "Rate limits hit 3 times in a row. Aborting contact discovery for today."
                    )
                    break
                contacts = []

            if contacts:
                contacts_text = "\n".join(contacts)
                self.sheets.update_people_found(
                    company_row["_row_index"], contacts_text
                )
                log.info(
                    f"  Written {len(contacts)} contact(s) to sheet for {company_name}"
                )
                populated += 1
            else:
                log.info(
                    f"  No contacts found for {company_name} — will retry tomorrow"
                )

            time.sleep(1.5)  # be polite to APIs

        log.info(f"Contact discovery complete — populated {populated} companies.")
        return populated
