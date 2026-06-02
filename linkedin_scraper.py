"""
linkedin_scraper.py — Stealth Playwright-based LinkedIn scraper

Scrolls the LinkedIn feed, searches posts, and browses the Jobs section
to discover hiring opportunities. Extracts poster name/title/company and
emails directly from LinkedIn when available.

Anti-detection: persistent cookie auth, navigator patches, Gaussian random
delays, human-like scrolling, viewport jitter, mouse simulation.

Usage:
    python linkedin_scraper.py              # first run — opens browser for manual login
    python linkedin_scraper.py --headless   # subsequent runs with saved session
"""

from dotenv import load_dotenv

load_dotenv()

import os
import re
import json
import time
import random
import logging
import argparse
from datetime import datetime, date
from typing import Optional

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("linkedin_scraper.log"), logging.StreamHandler()],
)

# ── Configuration ─────────────────────────────────────────────────────────────
LINKEDIN_ENABLED = os.environ.get("LINKEDIN_ENABLED", "true").lower() == "true"
LINKEDIN_MAX_POSTS = int(os.environ.get("LINKEDIN_MAX_POSTS", "20"))

DEFAULT_SEARCH_KEYWORDS = [
    "hiring AI engineer",
    "hiring ML engineer",
    "hiring AI/ML engineer",
    "hiring data scientist",
    "looking for AI engineer",
    "looking for ML engineer",
    "looking for data scientist",
    "open role AI engineer",
    "open role ML engineer",
    "open role data scientist",
]

LINKEDIN_SEARCH_KEYWORDS = os.environ.get("LINKEDIN_SEARCH_KEYWORDS", "")
if LINKEDIN_SEARCH_KEYWORDS.strip():
    SEARCH_KEYWORDS = [k.strip() for k in LINKEDIN_SEARCH_KEYWORDS.split(",") if k.strip()]
else:
    SEARCH_KEYWORDS = DEFAULT_SEARCH_KEYWORDS

# Jobs section keywords (shorter, for LinkedIn Jobs search bar)
JOBS_KEYWORDS = [
    "AI Engineer",
    "ML Engineer",
    "AI/ML Engineer",
    "Data Scientist",
]

# ── Hiring signal detection ──────────────────────────────────────────────────
HIRING_SIGNALS = [
    "hiring", "we're hiring", "we are hiring", "join our team",
    "looking for", "looking to hire", "open role", "open position",
    "job opening", "job opportunity", "apply now", "apply here",
    "dm me", "send your resume", "send your cv", "drop your resume",
    "reach out", "interested candidates", "we need", "come join",
    "join us", "opportunity", "openings", "recruiting",
    "hot job", "urgent hiring", "immediate opening", "walk-in",
    "#hiring", "#opentowork", "#jobopening", "#jobalert",
]

ROLE_KEYWORDS = [
    "ai engineer", "artificial intelligence engineer", "machine learning engineer", "ml engineer",
    "ai/ml engineer", "data scientist", "deep learning engineer", "nlp engineer",
    "computer vision engineer", "generative ai engineer", "llm engineer", "applied ai engineer",
    "data science"
]

EXPERIENCE_KEYWORDS = [
    "junior", "entry level", "entry-level", "fresher", "fresh graduate",
    "0-1 year", "0-2 year", "1 year", "1+ year", "0-1 yr", "0-2 yr",
    "1-2 year", "1-3 year", "associate", "early career",
    "new grad", "recent graduate", "intern", "trainee",
    "less than 1 year", "less than 2 year",
]

# Email regex
EMAIL_REGEX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# ── Stealth JavaScript ────────────────────────────────────────────────────────
STEALTH_JS = """
() => {
    // 1. Remove navigator.webdriver flag (Playwright sets this to true)
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Fake navigator.plugins (empty in headless, populated in real Chrome)
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                {
                    0: {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format'},
                    name: 'Chrome PDF Plugin',
                    filename: 'internal-pdf-viewer',
                    description: 'Portable Document Format',
                    length: 1
                },
                {
                    0: {type: 'application/pdf', suffixes: 'pdf', description: ''},
                    name: 'Chrome PDF Viewer',
                    filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                    description: '',
                    length: 1
                },
                {
                    0: {type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable'},
                    name: 'Native Client',
                    filename: 'internal-nacl-plugin',
                    description: '',
                    length: 1
                }
            ];
            arr.length = 3;
            return arr;
        },
        configurable: true
    });

    // 3. Realistic navigator.languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });

    // 4. Mock window.chrome (missing in headless)
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            connect: function() { return {onMessage: {addListener: function(){}}, postMessage: function(){}}; },
            sendMessage: function() {}
        };
    }

    // 5. Permissions.query override (notifications check)
    if (navigator.permissions && navigator.permissions.query) {
        const origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (params) => {
            if (params.name === 'notifications') {
                return Promise.resolve({state: Notification.permission});
            }
            return origQuery(params);
        };
    }

    // 6. Realistic hardware info
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true
    });
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true
    });

    // 7. WebGL vendor/renderer (headless fingerprint fix)
    try {
        const getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Intel Inc.';
            if (param === 37446) return 'Intel Iris OpenGL Engine';
            return getParam.call(this, param);
        };
    } catch(e) {}

    // 8. Connection info (missing in headless)
    try {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false
            }),
            configurable: true
        });
    } catch(e) {}
}
"""


# ── Human-like delay utilities ────────────────────────────────────────────────
def _gaussian_delay(mean: float = 3.0, stddev: float = 1.2, minimum: float = 1.5, maximum: float = 8.0):
    """Sleep for a Gaussian-distributed random duration (more natural than uniform)."""
    delay = random.gauss(mean, stddev)
    delay = max(minimum, min(maximum, delay))
    time.sleep(delay)


def _short_delay():
    """Brief delay between small actions (typing, mouse movement)."""
    time.sleep(random.uniform(0.3, 1.2))


def _reading_pause():
    """Simulate a human reading/thinking pause (10-25 seconds)."""
    pause = random.uniform(10, 25)
    log.debug(f"Reading pause: {pause:.1f}s")
    time.sleep(pause)


# ── Main Scraper Class ────────────────────────────────────────────────────────
class LinkedInScraper:
    """
    Stealth LinkedIn scraper that discovers hiring opportunities from your feed,
    LinkedIn search, and the Jobs section. Extracts poster info and emails.
    """

    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.creds_dir = os.path.join(base_dir, ".creds")
        os.makedirs(self.creds_dir, exist_ok=True)

        self.state_file = os.path.join(base_dir, "linkedin_scraped_posts.json")
        self.browser_state_path = os.path.join(self.creds_dir, "linkedin_state.json")

        self.state = self._load_state()
        self.playwright_instance = None
        self.browser = None
        self.context = None
        self.page = None
        self._posts_scraped_this_session = 0
        self._profile_visits_this_session = 0
        self.MAX_PROFILE_VISITS = 5  # limit profile visits per session

    # ── State persistence ─────────────────────────────────────────────────────
    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Failed to load LinkedIn state: {e}. Starting fresh.")
        return {"scraped_posts": [], "last_scrape": None}

    def _save_state(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save LinkedIn state: {e}")

    def _is_post_scraped(self, post_id: str) -> bool:
        """Check if we've already processed this post."""
        return any(p.get("post_id") == post_id for p in self.state.get("scraped_posts", []))

    def _mark_post_scraped(self, lead: dict):
        """Record that we've processed this post."""
        self.state.setdefault("scraped_posts", []).append({
            "post_id": lead.get("post_id", ""),
            "post_url": lead.get("post_url", ""),
            "poster_name": lead.get("poster_name", ""),
            "company": lead.get("company", ""),
            "scraped_date": date.today().isoformat(),
        })
        # Keep only last 500 entries to prevent state file bloat
        if len(self.state["scraped_posts"]) > 500:
            self.state["scraped_posts"] = self.state["scraped_posts"][-500:]

    # ── Browser lifecycle ─────────────────────────────────────────────────────
    def _launch_browser(self, headless: bool = False, block_media: bool = True):
        """
        Launch Chromium browser.
        - Login (headless=False): persistent profile dir so cookies survive.
        - Scraping (headless=True): loads saved storage state JSON.
        """
        from playwright.sync_api import sync_playwright

        self.playwright_instance = sync_playwright().start()

        width = random.randint(1280, 1440)
        height = random.randint(800, 900)

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        if not headless:
            # HEADED: use persistent profile dir (cookies auto-saved like real Chrome)
            profile_dir = os.path.join(self.creds_dir, "linkedin_profile")
            os.makedirs(profile_dir, exist_ok=True)

            self.context = self.playwright_instance.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=launch_args,
                viewport={"width": width, "height": height},
                user_agent=user_agent,
                locale="en-US",
                timezone_id="Asia/Kolkata",
                java_script_enabled=True,
                bypass_csp=True,
            )
            self.browser = None  # persistent context has no separate browser object
            self.context.add_init_script(STEALTH_JS)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        else:
            # HEADLESS: launch normally, load saved state if available
            self.browser = self.playwright_instance.chromium.launch(
                headless=False,
                args=launch_args,
            )
            ctx_opts = {
                "viewport": {"width": width, "height": height},
                "user_agent": user_agent,
                "locale": "en-US",
                "timezone_id": "Asia/Kolkata",
                "java_script_enabled": True,
                "bypass_csp": True,
            }
            if os.path.exists(self.browser_state_path):
                ctx_opts["storage_state"] = self.browser_state_path

            self.context = self.browser.new_context(**ctx_opts)
            self.context.add_init_script(STEALTH_JS)
            self.page = self.context.new_page()

        # Block images/fonts only during headless scraping
        if block_media and headless:
            self.page.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf}", lambda route: route.abort())

        log.info(f"Browser launched (headless={headless}, viewport={width}x{height})")

    def _save_browser_state(self):
        """Save cookies and localStorage for future sessions."""
        try:
            self.context.storage_state(path=self.browser_state_path)
            log.info("Browser state saved.")
        except Exception as e:
            log.warning(f"Failed to save browser state: {e}")

    def close(self):
        """Clean up browser resources."""
        try:
            if self.context:
                self._save_browser_state()
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright_instance:
                self.playwright_instance.stop()
        except Exception as e:
            log.warning(f"Browser cleanup error: {e}")
        self.page = None
        self.context = None
        self.browser = None
        self.playwright_instance = None

    # ── Human simulation ──────────────────────────────────────────────────────
    def _human_scroll(self, distance: Optional[int] = None):
        """Scroll the page like a human — variable distance, multi-step."""
        if not self.page:
            return
        if distance is None:
            distance = random.randint(350, 850)

        steps = random.randint(3, 8)
        step_size = distance // steps

        for i in range(steps):
            self.page.mouse.wheel(0, step_size + random.randint(-30, 30))
            time.sleep(random.uniform(0.08, 0.25))

        # Occasional micro-pause after scrolling (like reading)
        if random.random() < 0.3:
            time.sleep(random.uniform(1.5, 4.0))

    def _random_mouse_move(self):
        """Move mouse to a random position on screen."""
        if not self.page:
            return
        try:
            vp = self.page.viewport_size
            if vp:
                x = random.randint(100, vp["width"] - 100)
                y = random.randint(100, vp["height"] - 100)
                self.page.mouse.move(x, y, steps=random.randint(5, 15))
        except Exception:
            pass

    def _safe_click(self, selector: str, timeout: int = 5000) -> bool:
        """Click an element with human-like delay and offset."""
        try:
            element = self.page.wait_for_selector(selector, timeout=timeout)
            if element:
                _short_delay()
                # Click with slight random offset from center
                box = element.bounding_box()
                if box:
                    x_offset = random.randint(-3, 3)
                    y_offset = random.randint(-3, 3)
                    element.click(position={"x": box["width"] / 2 + x_offset, "y": box["height"] / 2 + y_offset})
                else:
                    element.click()
                return True
        except Exception:
            pass
        return False

    # ── Login management ──────────────────────────────────────────────────────
    def _is_logged_in(self) -> bool:
        """Check if the current page indicates a logged-in LinkedIn session."""
        try:
            url = self.page.url.lower()
            log.debug(f"Login check — current URL: {url}")

            # Definite NOT logged in pages
            if "/login" in url or "/checkpoint" in url or "linkedin.com/uas" in url:
                return False

            # URL-based detection (most reliable — these pages only load when logged in)
            logged_in_paths = ["/feed", "/mynetwork", "/messaging", "/notifications", "/jobs", "/in/"]
            if any(path in url for path in logged_in_paths):
                log.debug(f"URL-based login detected: {url}")
                return True

            # DOM-based fallback: check for navigation/feed elements
            feed_indicator = self.page.query_selector(
                "div.feed-shared-update-v2, "
                "div[data-finite-scroll-hotkey-context], "
                "input[aria-label='Search'], "
                "div.global-nav, "
                "nav[aria-label='Primary'], "
                "div[data-test-global-nav-item], "
                "header.global-nav, "
                "div#global-nav, "
                "nav.global-nav"
            )
            if feed_indicator:
                log.debug("DOM-based login detected.")
                return True

            return False
        except Exception as e:
            log.debug(f"Login check error: {e}")
            return False

    def login_interactive(self) -> bool:
        """
        First-time login: opens a browser with a persistent profile.
        User logs in manually, then presses Enter in the terminal to save.
        """
        print()
        print("=" * 60)
        print("  LINKEDIN LOGIN")
        print("=" * 60)
        print()
        print("  A Chromium browser window will open.")
        print("  >>> Log in to LinkedIn in THAT browser window <<<")
        print("  (It is a SEPARATE browser, not your regular Chrome)")
        print()
        print("  After you see your LinkedIn feed, come back to")
        print("  this terminal and press ENTER to save the session.")
        print()
        print("=" * 60)

        self._launch_browser(headless=False, block_media=False)
        self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        # Wait for user confirmation
        try:
            input("\n  >>> Press ENTER here after you have logged in to LinkedIn... ")
        except EOFError:
            # Non-interactive mode — fall back to auto-polling
            log.info("Non-interactive mode. Polling for login...")
            max_wait = 300
            waited = 0
            while waited < max_wait:
                time.sleep(3)
                waited += 3
                if self._is_logged_in():
                    break
                if waited % 30 == 0:
                    log.info(f"Waiting for login... ({waited}s / {max_wait}s)")
            else:
                log.error("Login timed out.")
                self.close()
                return False

        # Check and save
        try:
            current_url = self.page.url
            log.info(f"URL after login: {current_url}")
        except Exception:
            pass

        log.info("Saving LinkedIn session...")
        time.sleep(2)
        self._save_browser_state()
        self.close()

        if os.path.exists(self.browser_state_path):
            log.info("LinkedIn session saved successfully!")
            print("\n  Session saved! Future runs will be automatic and headless.\n")
            return True
        else:
            log.error("Session file not created.")
            print("\n  Session save failed. Please try again.\n")
            return False

    def _ensure_logged_in(self) -> bool:
        """Navigate to LinkedIn and verify login. Returns False if login failed."""
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        _gaussian_delay(mean=3)

        if self._is_logged_in():
            log.info("LinkedIn session is valid.")
            return True

        log.warning("LinkedIn session expired or invalid. Attempting re-login...")
        self.close()
        return self.login_interactive()

    # ── Email extraction ──────────────────────────────────────────────────────
    def _extract_emails_from_text(self, text: str) -> list[str]:
        """Extract email addresses from post/profile text using regex."""
        if not text:
            return []
        found = EMAIL_REGEX.findall(text)
        # Filter out obviously fake/system emails
        filtered = []
        for email in found:
            email_lower = email.lower()
            if any(skip in email_lower for skip in [
                "example.com", "test.com", "noreply", "no-reply",
                "linkedin.com", "email.com", "domain.com",
                ".png", ".jpg", ".gif", ".svg",
            ]):
                continue
            filtered.append(email)
        return filtered

    def _extract_email_from_profile(self, profile_url: str) -> Optional[str]:
        """
        Visit a LinkedIn profile and try to extract email from the Contact Info section.
        Limited to MAX_PROFILE_VISITS per session to avoid detection.
        """
        if self._profile_visits_this_session >= self.MAX_PROFILE_VISITS:
            log.debug("Profile visit limit reached for this session.")
            return None

        if not profile_url or "linkedin.com/in/" not in profile_url:
            return None

        try:
            self._profile_visits_this_session += 1
            _gaussian_delay(mean=4, minimum=3, maximum=7)
            self._random_mouse_move()

            self.page.goto(profile_url, wait_until="domcontentloaded")
            _gaussian_delay(mean=3, minimum=2, maximum=5)

            # 1. Check About section / Body for emails FIRST
            try:
                body_text = self.page.inner_text("body")
                emails = self._extract_emails_from_text(body_text)
                if emails:
                    log.info(f"  Email found in profile text (About/Summary): {emails[0]}")
                    return emails[0]
            except Exception:
                pass

            # 2. Try to find and click the "Contact info" link
            contact_selectors = [
                "a[href*='contact-info']",
                "a#top-card-text-details-contact-info",
                "a[data-control-name='contact_see_more']",
            ]

            clicked = False
            for selector in contact_selectors:
                if self._safe_click(selector, timeout=3000):
                    clicked = True
                    break

            if not clicked:
                # Try finding by text content
                try:
                    links = self.page.query_selector_all("a")
                    for link in links:
                        text = (link.inner_text() or "").strip().lower()
                        if "contact info" in text:
                            link.click()
                            clicked = True
                            break
                except Exception:
                    pass

            modal_content = ""
            portfolio_links = []

            # Extract any external links from the main profile top card before we process modal
            try:
                top_links = self.page.query_selector_all("div.ph5 a")
                for link in top_links:
                    href = link.get_attribute("href")
                    if href and href.startswith("http") and "linkedin.com" not in href:
                        portfolio_links.append(href)
            except Exception:
                pass

            if clicked:
                _gaussian_delay(mean=2, minimum=1.5, maximum=3)

                # Extract email and links from the contact info modal
                modal_selectors = [
                    "section.ci-email",
                    "div.ci-email",
                    "section[data-section='email']",
                    "div.pv-contact-info__ci-container",
                    "div[aria-modal='true']",
                    "div.artdeco-modal__content",
                ]

                for selector in modal_selectors:
                    try:
                        el = self.page.wait_for_selector(selector, timeout=3000)
                        if el:
                            modal_content += " " + (el.inner_text() or "")
                    except Exception:
                        continue

                # If no specific modal found, try the whole page body
                if not modal_content.strip():
                    try:
                        modal_content = self.page.inner_text("body")
                    except Exception:
                        pass

                # Extract external links from the modal
                try:
                    modal_links = self.page.query_selector_all("div.artdeco-modal__content a")
                    for link in modal_links:
                        href = link.get_attribute("href")
                        if href and href.startswith("http") and "linkedin.com" not in href:
                            portfolio_links.append(href)
                except Exception:
                    pass

                emails = self._extract_emails_from_text(modal_content)
                if emails:
                    log.info(f"  Email found on profile Contact Info: {emails[0]}")
                    try:
                        self._safe_click("button[aria-label='Dismiss']", timeout=2000)
                    except Exception:
                        pass
                    return emails[0]

                # Close the modal before leaving
                try:
                    self._safe_click("button[aria-label='Dismiss']", timeout=2000)
                except Exception:
                    pass

            # 3. If no email yet, visit the portfolio/external links in a new tab
            # Deduplicate links
            portfolio_links = list(set(portfolio_links))
            for link in portfolio_links:
                try:
                    log.info(f"  Visiting portfolio/website to find email: {link}")
                    new_page = self.context.new_page()
                    new_page.goto(link, wait_until="domcontentloaded", timeout=15000)
                    _gaussian_delay(mean=3)
                    
                    portfolio_text = new_page.inner_text("body")
                    site_emails = self._extract_emails_from_text(portfolio_text)
                    new_page.close()

                    if site_emails:
                        log.info(f"  Email found on portfolio: {site_emails[0]}")
                        return site_emails[0]
                except Exception as e:
                    log.debug(f"Portfolio visit failed: {e}")
                    try:
                        new_page.close()
                    except Exception:
                        pass

            return None

        except Exception as e:
            log.debug(f"Profile email extraction failed for {profile_url}: {e}")
            return None

    # ── Post relevance detection ──────────────────────────────────────────────
    def _is_hiring_post(self, text: str) -> bool:
        """Check if a post text contains hiring signals AND relevant role keywords."""
        if not text:
            return False
        text_lower = text.lower()

        has_hiring_signal = any(signal in text_lower for signal in HIRING_SIGNALS)
        has_role_keyword = any(kw in text_lower for kw in ROLE_KEYWORDS)

        return has_hiring_signal and has_role_keyword

    def _parse_headline(self, headline: str) -> tuple[str, str]:
        """
        Parse a LinkedIn headline like 'Engineering Manager at Acme AI'
        into (title, company). Returns ('', '') if unparseable.
        """
        if not headline:
            return ("", "")

        # Common patterns: "Title at Company", "Title | Company", "Title @ Company"
        for sep in [" at ", " @ ", " | ", " — ", " - ", " · "]:
            if sep in headline:
                parts = headline.split(sep, 1)
                return (parts[0].strip(), parts[1].strip())

        return (headline.strip(), "")

    # ── Job Application ────────────────────────────────────────────────────────
    def _easy_apply_to_job(self) -> bool:
        """
        Attempts to complete the Easy Apply flow on the current job page.
        Returns True if application was submitted successfully.
        """
        try:
            # Look for Easy Apply button
            apply_btn_selector = "button.jobs-apply-button:has-text('Easy Apply'), button.jobs-apply-button"
            btn = self.page.query_selector(apply_btn_selector)
            if not btn:
                log.debug("No Easy Apply button found on page.")
                return False

            self._safe_click(apply_btn_selector, timeout=3000)
            _gaussian_delay(mean=3, minimum=2, maximum=4)

            # Wait for modal
            modal_selector = ".jobs-easy-apply-modal, div[data-test-modal]"
            try:
                self.page.wait_for_selector(modal_selector, timeout=5000)
            except Exception:
                log.debug("Easy apply modal did not open.")
                return False

            max_steps = 10
            for step in range(max_steps):
                _gaussian_delay(mean=2, minimum=1, maximum=3)

                # Check if we reached the final "Submit application"
                submit_btn = self.page.query_selector("button:has-text('Submit application')")
                if submit_btn and submit_btn.is_visible():
                    log.info("  Submitting application...")
                    submit_btn.click()
                    _gaussian_delay(mean=3, minimum=2, maximum=5)
                    # Close success modal
                    try:
                        self._safe_click("button[aria-label='Dismiss']", timeout=3000)
                    except Exception:
                        pass
                    return True

                # Look for 'Next' or 'Review'
                next_btn = self.page.query_selector("button:has-text('Next'), button:has-text('Review')")
                if not next_btn:
                    log.debug("No Next/Review button found. Might be stuck.")
                    break

                next_btn.click()
                _gaussian_delay(mean=2)

                # Check for errors indicating required fields
                error_msgs = self.page.query_selector_all(".artdeco-inline-feedback--error")
                if error_msgs:
                    log.info("  Form requires input. Attempting to auto-fill...")
                    
                    # Fill text/number inputs
                    text_inputs = self.page.query_selector_all("input[type='text'], input[type='number']")
                    for t_in in text_inputs:
                        if t_in.is_visible():
                            try:
                                val = t_in.input_value()
                                if not val:
                                    t_in.fill("2")
                            except Exception:
                                pass

                    # Fill radio buttons (Pick 'Yes' or first option)
                    radio_groups = self.page.query_selector_all("fieldset")
                    for fs in radio_groups:
                        yes_radio = fs.query_selector("label:has-text('Yes'), input[type='radio']")
                        if yes_radio and yes_radio.is_visible():
                            try:
                                yes_radio.click()
                            except Exception:
                                pass

                    # Fill dropdowns
                    selects = self.page.query_selector_all("select")
                    for sel in selects:
                        if sel.is_visible():
                            try:
                                options = sel.query_selector_all("option")
                                if len(options) > 1:
                                    val = options[1].get_attribute("value")
                                    sel.select_option(value=val)
                            except Exception:
                                pass

                    _gaussian_delay(mean=2)
                    next_btn.click()
                    _gaussian_delay(mean=2)

                    # If still errors, abort
                    error_msgs_after = self.page.query_selector_all(".artdeco-inline-feedback--error")
                    if error_msgs_after:
                        log.info("  Could not bypass required fields. Aborting application.")
                        break

            # If we exit without returning True, abort the modal
            try:
                close_btn = self.page.query_selector("button[aria-label='Dismiss']")
                if close_btn:
                    close_btn.click()
                    _gaussian_delay(mean=1)
                    discard_btn = self.page.query_selector("button[data-control-name='discard_application_confirm_btn']")
                    if discard_btn:
                        discard_btn.click()
            except Exception:
                pass

            return False

        except Exception as e:
            log.debug(f"Easy Apply failed: {e}")
            return False

    # ── Feed scraping ─────────────────────────────────────────────────────────
    def scrape_feed(self) -> list[dict]:
        """
        Scroll the LinkedIn home feed and extract hiring posts.
        Returns a list of lead dicts.
        """
        log.info("=== Scraping LinkedIn Feed ===")
        leads = []

        try:
            self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            _gaussian_delay(mean=4, minimum=3, maximum=6)

            if not self._is_logged_in():
                log.error("Not logged in during feed scrape. Aborting.")
                return leads

            max_scrolls = 12
            for scroll_num in range(max_scrolls):
                if self._posts_scraped_this_session >= LINKEDIN_MAX_POSTS:
                    log.info(f"Reached max posts limit ({LINKEDIN_MAX_POSTS}). Stopping feed scrape.")
                    break

                # Extract posts from current viewport
                new_leads = self._extract_posts_from_page("linkedin_feed")
                leads.extend(new_leads)

                # Scroll down
                self._human_scroll()
                _gaussian_delay(mean=3.5, minimum=2.5, maximum=6)

                # Occasional mouse movement to seem human
                if random.random() < 0.4:
                    self._random_mouse_move()

                # Occasional reading pause (every ~4 scrolls)
                if scroll_num > 0 and scroll_num % 4 == 0 and random.random() < 0.5:
                    _reading_pause()

                log.debug(f"Feed scroll {scroll_num + 1}/{max_scrolls}, total leads so far: {len(leads)}")

        except Exception as e:
            log.error(f"Feed scraping error: {e}")

        log.info(f"Feed scraping complete. Found {len(leads)} leads.")
        return leads

    # ── Search-based scraping ─────────────────────────────────────────────────
    def scrape_search(self) -> list[dict]:
        """
        Search LinkedIn for posts containing hiring keywords.
        Returns a list of lead dicts.
        """
        log.info("=== Scraping LinkedIn Search ===")
        leads = []

        # Pick 3 random keywords from our list (don't search all — too suspicious)
        keywords_to_search = random.sample(
            SEARCH_KEYWORDS, min(3, len(SEARCH_KEYWORDS))
        )

        for keyword in keywords_to_search:
            if self._posts_scraped_this_session >= LINKEDIN_MAX_POSTS:
                break

            try:
                encoded = keyword.replace(" ", "%20")
                search_url = f"https://www.linkedin.com/search/results/content/?keywords={encoded}&sortBy=%22date_posted%22"

                log.info(f"Searching: '{keyword}'")
                _gaussian_delay(mean=4, minimum=3, maximum=6)
                self.page.goto(search_url, wait_until="domcontentloaded")
                _gaussian_delay(mean=4, minimum=3, maximum=7)

                # Scroll a few times to load results
                for scroll_num in range(5):
                    if self._posts_scraped_this_session >= LINKEDIN_MAX_POSTS:
                        break

                    new_leads = self._extract_posts_from_page("linkedin_search")
                    leads.extend(new_leads)

                    self._human_scroll()
                    _gaussian_delay(mean=3.5, minimum=2.5, maximum=6)

                    if random.random() < 0.3:
                        self._random_mouse_move()

                # Pause between different searches
                _gaussian_delay(mean=5, minimum=4, maximum=8)

            except Exception as e:
                log.warning(f"Search scraping error for '{keyword}': {e}")

        log.info(f"Search scraping complete. Found {len(leads)} leads.")
        return leads

    # ── Jobs section scraping ─────────────────────────────────────────────────
    def scrape_jobs(self) -> list[dict]:
        """
        Browse the LinkedIn Jobs section filtered by role keywords and
        entry-level experience. Extracts job poster info.
        Returns a list of lead dicts.
        """
        log.info("=== Scraping LinkedIn Jobs ===")
        leads = []

        # Search all job keywords (since there are only 4 now, we can search all instead of random.sample)
        keywords_to_search = JOBS_KEYWORDS

        for keyword in keywords_to_search:
            try:
                encoded = keyword.replace(" ", "%20")
                # f_E=1,2 = Internship + Entry level; f_AL=true = Easy Apply; f_TPR=r604800 = Past week
                jobs_url = (
                    f"https://www.linkedin.com/jobs/search/"
                    f"?keywords={encoded}&f_E=1%2C2&f_AL=true&f_TPR=r604800&sortBy=DD"
                )

                log.info(f"Searching jobs: '{keyword}'")
                _gaussian_delay(mean=4, minimum=3, maximum=6)
                self.page.goto(jobs_url, wait_until="domcontentloaded")
                _gaussian_delay(mean=5, minimum=3, maximum=7)

                # Extract job cards from the page
                new_leads = self._extract_jobs_from_page()
                leads.extend(new_leads)

                # Scroll to load more jobs (no hard limit, try up to 40 scrolls/pages)
                consecutive_empty_scrolls = 0
                for scroll_num in range(40):
                    self._human_scroll(distance=random.randint(400, 700))
                    _gaussian_delay(mean=3, minimum=2, maximum=5)

                    more_leads = self._extract_jobs_from_page()
                    if not more_leads:
                        consecutive_empty_scrolls += 1
                        if consecutive_empty_scrolls >= 3:
                            # Break if we've scrolled 3 times and found no new jobs (end of results)
                            break
                    else:
                        consecutive_empty_scrolls = 0
                        leads.extend(more_leads)

                    if random.random() < 0.3:
                        self._random_mouse_move()

                # Pause between different job searches
                _gaussian_delay(mean=5, minimum=4, maximum=8)

            except Exception as e:
                log.warning(f"Jobs scraping error for '{keyword}': {e}")

        log.info(f"Jobs scraping complete. Found {len(leads)} leads.")
        return leads

    # ── Post extraction engine ────────────────────────────────────────────────
    def _extract_posts_from_page(self, source: str) -> list[dict]:
        """
        Extract all visible hiring posts from the current page using JavaScript.
        This approach is more resilient than CSS selectors since we look for
        semantic patterns in the DOM.
        """
        leads = []

        try:
            # Use JavaScript to extract structured data from all visible posts
            raw_posts = self.page.evaluate("""
                () => {
                    const posts = [];

                    // Strategy 1: Find posts by data-urn attribute (most reliable)
                    document.querySelectorAll('[data-urn]').forEach(el => {
                        const urn = el.getAttribute('data-urn') || '';
                        if (!urn.includes('activity') && !urn.includes('ugcPost')) return;

                        // Get poster info
                        const actorEl = el.querySelector(
                            '.update-components-actor__name, ' +
                            '.feed-shared-actor__name, ' +
                            'span[aria-hidden="true"]'
                        );
                        const headlineEl = el.querySelector(
                            '.update-components-actor__description, ' +
                            '.feed-shared-actor__description, ' +
                            '.update-components-actor__sub-description'
                        );
                        const textEl = el.querySelector(
                            '.feed-shared-text, ' +
                            '.update-components-text, ' +
                            'div[dir="ltr"] span, ' +
                            'span.break-words'
                        );
                        const linkEl = el.querySelector(
                            'a.update-components-actor__meta-link, ' +
                            'a.feed-shared-actor__container-link, ' +
                            'a[data-control-name="actor"], ' +
                            'a[href*="/in/"]'
                        );

                        const name = actorEl ? actorEl.innerText.trim() : '';
                        const headline = headlineEl ? headlineEl.innerText.trim() : '';
                        const text = textEl ? textEl.innerText.trim() : '';
                        const profileUrl = linkEl ? linkEl.href : '';

                        if (name && text.length > 20) {
                            posts.push({
                                post_id: urn,
                                poster_name: name.split('\\n')[0].trim(),
                                headline: headline.split('\\n')[0].trim(),
                                post_text: text.substring(0, 2000),
                                profile_url: profileUrl,
                            });
                        }
                    });

                    // Strategy 2: Fallback — look for post containers by class patterns
                    if (posts.length === 0) {
                        document.querySelectorAll(
                            '.feed-shared-update-v2, ' +
                            '.occludable-update, ' +
                            'div[class*="feed-shared"]'
                        ).forEach(el => {
                            const nameEl = el.querySelector('span[aria-hidden="true"], strong');
                            const textEl = el.querySelector('span.break-words, div[dir="ltr"]');
                            const linkEl = el.querySelector('a[href*="/in/"]');

                            const name = nameEl ? nameEl.innerText.trim() : '';
                            const text = textEl ? textEl.innerText.trim() : '';
                            const profileUrl = linkEl ? linkEl.href : '';

                            if (name && text.length > 20) {
                                posts.push({
                                    post_id: 'fallback_' + Math.random().toString(36).substr(2, 9),
                                    poster_name: name.split('\\n')[0].trim(),
                                    headline: '',
                                    post_text: text.substring(0, 2000),
                                    profile_url: profileUrl,
                                });
                            }
                        });
                    }

                    return posts;
                }
            """)
        except Exception as e:
            log.warning(f"Post extraction JS error: {e}")
            return leads

        for raw in raw_posts:
            if self._posts_scraped_this_session >= LINKEDIN_MAX_POSTS:
                break

            post_id = raw.get("post_id", "")
            post_text = raw.get("post_text", "")
            poster_name = raw.get("poster_name", "")

            # Skip already-scraped posts
            if self._is_post_scraped(post_id):
                continue

            # Skip non-hiring posts
            if not self._is_hiring_post(post_text):
                continue

            # Parse headline into title and company
            headline = raw.get("headline", "")
            poster_title, company = self._parse_headline(headline)

            # Try to extract email directly from the post text
            post_emails = self._extract_emails_from_text(post_text)
            email = post_emails[0] if post_emails else None

            # If no email in post text, try the poster's profile contact info
            if not email and raw.get("profile_url"):
                email = self._extract_email_from_profile(raw["profile_url"])
                # Navigate back to feed/search after profile visit
                if email:
                    _gaussian_delay(mean=3, minimum=2, maximum=5)
                    self.page.go_back()
                    _gaussian_delay(mean=2)

            lead = {
                "poster_name": poster_name,
                "poster_title": poster_title,
                "company": company,
                "post_text": post_text[:500],  # Truncate for sheet storage
                "post_url": f"https://www.linkedin.com/feed/update/{post_id}" if "urn:" in post_id else "",
                "profile_url": raw.get("profile_url", ""),
                "email": email,
                "source": source,
                "scraped_date": date.today().isoformat(),
                "post_id": post_id,
            }

            self._mark_post_scraped(lead)
            leads.append(lead)
            self._posts_scraped_this_session += 1
            log.info(
                f"  [LinkedIn] {poster_name} @ {company} | "
                f"Email: {email or 'N/A'} | Source: {source}"
            )

        return leads

    def _extract_jobs_from_page(self) -> list[dict]:
        """Extract job postings from the LinkedIn Jobs search results page."""
        leads = []

        try:
            raw_jobs = self.page.evaluate("""
                () => {
                    const jobs = [];

                    // Job cards are typically in list items within the scaffold
                    const cards = document.querySelectorAll(
                        '.job-card-container, ' +
                        '.jobs-search-results__list-item, ' +
                        'li[class*="jobs-search"], ' +
                        'div[data-job-id]'
                    );

                    cards.forEach(card => {
                        const titleEl = card.querySelector(
                            '.job-card-list__title, ' +
                            'a[class*="job-card"] strong, ' +
                            'a.job-card-container__link strong, ' +
                            'strong'
                        );
                        const companyEl = card.querySelector(
                            '.job-card-container__primary-description, ' +
                            '.artdeco-entity-lockup__subtitle, ' +
                            'a[class*="company"], ' +
                            'span.job-card-container__primary-description'
                        );
                        const locationEl = card.querySelector(
                            '.job-card-container__metadata-item, ' +
                            '.artdeco-entity-lockup__caption, ' +
                            'li[class*="metadata"]'
                        );
                        const linkEl = card.querySelector('a[href*="/jobs/view/"]');
                        const jobId = card.getAttribute('data-job-id') ||
                                      (linkEl ? linkEl.href.match(/\\/jobs\\/view\\/(\\d+)/)?.[1] : '') ||
                                      'job_' + Math.random().toString(36).substr(2, 9);

                        const title = titleEl ? titleEl.innerText.trim() : '';
                        const company = companyEl ? companyEl.innerText.trim() : '';
                        const location = locationEl ? locationEl.innerText.trim() : '';
                        const jobUrl = linkEl ? linkEl.href : '';

                        if (title && company) {
                            jobs.push({
                                job_id: jobId,
                                job_title: title,
                                company: company.split('\\n')[0].trim(),
                                location: location.split('\\n')[0].trim(),
                                job_url: jobUrl,
                            });
                        }
                    });

                    return jobs;
                }
            """)
        except Exception as e:
            log.warning(f"Job extraction JS error: {e}")
            return leads

        for raw in raw_jobs:
            job_id = f"job_{raw.get('job_id', '')}"

            if self._is_post_scraped(job_id):
                continue

            company = raw.get("company", "")
            job_title = raw.get("job_title", "")

            # Check if the job title matches our target roles
            title_lower = job_title.lower()
            if not any(kw in title_lower for kw in ROLE_KEYWORDS):
                continue

            # Try to get recruiter info by clicking on the job card
            email = None
            poster_name = ""
            poster_title = ""
            job_url = raw.get("job_url", "")

            if job_url and self._profile_visits_this_session < self.MAX_PROFILE_VISITS:
                try:
                    self._profile_visits_this_session += 1
                    _gaussian_delay(mean=3, minimum=2, maximum=5)
                    self.page.goto(job_url, wait_until="domcontentloaded")
                    _gaussian_delay(mean=3, minimum=2, maximum=5)

                    # Try to find the job poster / recruiter info
                    recruiter_info = self.page.evaluate("""
                        () => {
                            // Look for "Meet the hiring team" or job poster section
                            const sections = document.querySelectorAll(
                                '.jobs-poster__name, ' +
                                '.hirer-card__hirer-information, ' +
                                'div[class*="hiring-team"], ' +
                                'div[class*="poster"], ' +
                                'a[href*="/in/"][class*="hirer"]'
                            );

                            for (const section of sections) {
                                const nameEl = section.querySelector('strong, span[aria-hidden="true"], a');
                                const titleEl = section.querySelector('span[class*="subtitle"], span:not(:first-child)');

                                if (nameEl) {
                                    return {
                                        name: nameEl.innerText.trim().split('\\n')[0],
                                        title: titleEl ? titleEl.innerText.trim().split('\\n')[0] : '',
                                        profileUrl: nameEl.href || section.querySelector('a')?.href || ''
                                    };
                                }
                            }

                            // Fallback: look for any profile link in the job detail
                            const profileLink = document.querySelector(
                                '.jobs-poster a[href*="/in/"], ' +
                                'div[class*="hirer"] a[href*="/in/"]'
                            );
                            if (profileLink) {
                                return {
                                    name: profileLink.innerText.trim().split('\\n')[0],
                                    title: '',
                                    profileUrl: profileLink.href
                                };
                            }

                            return null;
                        }
                    """)

                    if recruiter_info:
                        poster_name = recruiter_info.get("name", "")
                        poster_title = recruiter_info.get("title", "")

                        # Try to get email from recruiter's profile
                        profile_url = recruiter_info.get("profileUrl", "")
                        if profile_url and "linkedin.com/in/" in profile_url:
                            email = self._extract_email_from_profile(profile_url)

                    # Also check the job description text for emails
                    try:
                        job_desc = self.page.inner_text("div.jobs-description, div[class*='description']")
                        desc_emails = self._extract_emails_from_text(job_desc)
                        if desc_emails and not email:
                            email = desc_emails[0]
                    except Exception:
                        pass

                    # Attempt to auto-apply to the job
                    log.info(f"  Attempting Easy Apply for {job_title}")
                    applied = self._easy_apply_to_job()
                    if applied:
                        log.info("  ✓ Application submitted successfully.")

                    # Go back to job search results
                    self.page.go_back()
                    _gaussian_delay(mean=2, minimum=1.5, maximum=3)

                except Exception as e:
                    log.debug(f"Job detail extraction error: {e}")

            lead = {
                "poster_name": poster_name or "Hiring Manager",
                "poster_title": poster_title or "Recruiter",
                "company": company,
                "post_text": f"Job Posting: {job_title} at {company} ({raw.get('location', '')})",
                "post_url": job_url,
                "profile_url": "",
                "email": email,
                "source": "linkedin_jobs",
                "scraped_date": date.today().isoformat(),
                "post_id": job_id,
                "auto_applied": applied,
            }

            self._mark_post_scraped(lead)
            leads.append(lead)
            self._posts_scraped_this_session += 1
            log.info(
                f"  [Jobs] {poster_name or '?'} @ {company} — {job_title} | "
                f"Email: {email or 'N/A'}"
            )

        return leads

    # ── Main daily entry point ────────────────────────────────────────────────
    def run_daily_scrape(self) -> list[dict]:
        """
        Main entry point. Runs all three scraping modes and returns
        deduplicated leads.

        Returns:
            List of lead dicts, each containing:
            - poster_name, poster_title, company, post_text, post_url
            - email (str or None), source, scraped_date
        """
        if not LINKEDIN_ENABLED:
            log.info("LinkedIn scraping is disabled (LINKEDIN_ENABLED=false). Skipping.")
            return []

        log.info("=" * 60)
        log.info("=== LinkedIn Daily Scrape Started ===")
        log.info("=" * 60)

        all_leads = []
        self._posts_scraped_this_session = 0
        self._profile_visits_this_session = 0

        try:
            # Check if we have a saved browser session
            has_session = os.path.exists(self.browser_state_path)

            if not has_session:
                # First-time: interactive login required
                if not self.login_interactive():
                    log.error("LinkedIn login failed. Aborting scrape.")
                    return []
                # Re-launch in headless mode with saved session
                self._launch_browser(headless=False)
            else:
                self._launch_browser(headless=False)

            # Verify login is still valid
            if not self._ensure_logged_in():
                log.error("Cannot establish LinkedIn session. Aborting.")
                return []

            # ── Mode 1: Feed scroll ──
            if self._posts_scraped_this_session < LINKEDIN_MAX_POSTS:
                feed_leads = self.scrape_feed()
                all_leads.extend(feed_leads)

            # ── Mode 2: Search-based ──
            if self._posts_scraped_this_session < LINKEDIN_MAX_POSTS:
                _gaussian_delay(mean=5, minimum=4, maximum=8)
                search_leads = self.scrape_search()
                all_leads.extend(search_leads)

            # ── Mode 3: Jobs section ──
            # No limit check for jobs mode, search all
            _gaussian_delay(mean=5, minimum=4, maximum=8)
            jobs_leads = self.scrape_jobs()
            all_leads.extend(jobs_leads)

        except Exception as e:
            log.error(f"LinkedIn scraping failed: {e}", exc_info=True)
        finally:
            self._save_state()
            self.close()

        # Deduplicate by poster_name + company
        seen = set()
        unique_leads = []
        for lead in all_leads:
            key = f"{lead['poster_name'].lower()}|{lead['company'].lower()}"
            if key not in seen:
                seen.add(key)
                unique_leads.append(lead)

        log.info("=" * 60)
        log.info(f"=== LinkedIn Scrape Complete: {len(unique_leads)} unique leads ===")
        for lead in unique_leads:
            email_status = f"Email: {lead['email']}" if lead.get("email") else "Email: needs lookup"
            log.info(f"  {lead['poster_name']} @ {lead['company']} | {email_status} | {lead['source']}")
        log.info("=" * 60)

        return unique_leads


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn Scraper")
    parser.add_argument(
        "--login", action="store_true",
        help="Force interactive login (opens browser window)"
    )
    args = parser.parse_args()

    scraper = LinkedInScraper()

    if args.login:
        success = scraper.login_interactive()
        print(f"Login {'succeeded' if success else 'failed'}.")
    else:
        leads = scraper.run_daily_scrape()
        print(f"\nFound {len(leads)} leads:")
        for lead in leads:
            email_str = lead.get("email") or "needs lookup"
            print(f"  {lead['poster_name']} @ {lead['company']} | {email_str} | {lead['source']}")
