# Outreach Agent Knowledge Base & Architecture

This document serves as the technical knowledge base for the **Outreach Agent**. It explains the internal mechanics, state management, and fail-safes built into the system.

## 🧠 System Overview
The Outreach Agent is an autonomous, headless AI system designed to find relevant AI startups, scrape recruiter/engineering manager contacts, generate highly personalized cold outreach emails based on a user's resume, and send them via Gmail.

### Core Philosophy
1. **Zero Database Infrastructure**: Uses Google Sheets as the single source of truth (CRM) so the user can visually monitor the agent's work.
2. **Stateless Compute**: The Python script itself is completely stateless. It can crash, be killed, or reboot, and it will safely resume exactly where it left off by reading the Google Sheet.
3. **Defensive API usage**: Implements strict quota management to avoid burning free-tier API keys.

---

## 🏗️ Architecture & Modules

The system is split into modular components:

* `scheduler.py`: The entry point for background execution. Handles the 24-hour sleep loop (if running locally) or executes immediately (if triggered via cron on PythonAnywhere).
* `agent.py`: The "Controller" or brain. Orchestrates the workflow: checking backlogs, triggering lead generation, syncing replies, finding contacts, and sending emails.
* `sheets.py`: The Data Access Layer. Interfaces with the Google Sheets API. Reads the layout dynamically based on the exact structure of `job_search_tracker.xlsx`.
* `contact_finder.py`: Integrates with Hunter.io, Apollo.io, and Snov.io. Cascades through the APIs to find emails for a given company domain.
* `mailer.py`: Interfaces with the Gmail API via OAuth. Handles both sending plain-text/HTML emails and checking the inbox for replies using thread IDs.

---

## 💾 State Management (The Google Sheet)

The agent relies on a Google Sheet with two specific tabs:
1. **Company Tracker**: Stores high-level data about startups (Name, HQ, Why Target). Column K (`People Found`) stores the raw string output from the contact finder.
2. **Outreach Tracker**: The granular CRM. Every individual email sent gets a row here. This tracks the `Status`, `Opened?`, `Replied?`, and stores the personalized `Hook`.

### Deduplication
Before sending an email or finding a contact, `agent.py` pulls all rows from the `Outreach Tracker`. If an email address already exists in that sheet, the agent safely skips it.

---

## 🛡️ Fail-Safes & Quota Management

### 1. Backlog Limit
Lead Generation (finding new startups via Gemini) costs money/tokens. The agent checks the `Company Tracker` first. If there are **more than 20 companies** with an empty `People Found` column, the agent **skips Lead Generation** for the day. This prevents building an endless backlog of startups that we don't have the API credits to find contacts for.

### 2. Contact API Strike System
Free tiers for Hunter/Apollo/Snov are very limited. `contact_finder.py` implements a "Strike" system:
* If an API returns an `HTTP 429` (Rate Limited) or `HTTP 403` (Quota Exceeded), it registers a strike.
* If the agent hits **3 strikes in a row**, it completely aborts contact discovery for the day. This prevents the script from spamming dead APIs and getting the accounts permanently banned.

### 3. Email Warming Schedule
To protect the user's Gmail sender reputation from being marked as spam, `agent.py` limits the number of emails sent per day based on how long the campaign has been running:
* **Week 1**: Max 10 emails/day.
* **Weeks 2-3**: Max 25 emails/day.
* **Week 4+**: Max 50 emails/day.

---

## 🤖 AI Integration (Gemini 3.1 Flash)

The agent uses Gemini for three distinct tasks:

1. **Lead Generation**: Prompted to act as an AI startup researcher. Output is structured as strict JSON containing company name, HQ, and a specific technical reason to target them.
2. **Personalized Hooks**: Before sending an email, Gemini reads `profile.txt` and cross-references it with the startup's "Why Target" description. It generates a 1-sentence hyper-personalized hook tying the candidate's specific background (e.g., RAG, LLM latency reduction) to the startup's mission.
3. **Reply Classification**: When the agent detects a new reply in the Gmail inbox, it passes the email body to Gemini to classify the sentiment (e.g., `positive_interest`, `rejection`, `out_of_office`) and generates a suggested response. It logs this into the Sheet without auto-sending, keeping the human in the loop for the final conversation.

---

## 🧗 Development Journey: Issues Faced & Overcome

Building this system required overcoming several unique architectural and deployment challenges:

### 1. The Headless Authentication Problem
* **Issue**: The Gmail API uses OAuth2, which requires a browser window to pop up and ask for user consent to generate a `token.pickle` file. However, our target deployment environment (PythonAnywhere) is completely headless and lacks a browser.
* **Solution**: We implemented a split-authentication flow. The user runs the script locally on Windows first to trigger the browser popup and generate the `token.pickle`. Then, they push the project to PythonAnywhere (moving the token securely into a `.creds/` folder). The script on PythonAnywhere just consumes the pre-generated token to refresh access seamlessly.

### 2. The Google Sheets Alignment Conflict
* **Issue**: We originally tried to simplify the Google Sheets logic by forcing a "clean" CSV template onto the user. This broke because the user preferred their original, highly stylized `job_search_tracker.xlsx` layout which had blank spacer columns and different headers.
* **Solution**: We ran a background script to analyze the exact Pandas/OpenPyXL structure of the user's spreadsheet, and reverted the hardcoded column offsets in `sheets.py` (e.g., mapping "People Found" to column `K` instead of `G`). We then wrote an OpenPyXL cleanup script to sanitize the user's Excel file of personal data so it could be open-sourced as the master template.

### 3. The Runaway API Backlog
* **Issue**: The agent was programmed to unconditionally run the "Lead Generation" phase (using Gemini to find 10-20 new startups) at the start of every daily run. Because free-tier contact APIs (Hunter, Apollo) only allow a handful of searches per day, this created an endless, unreachable backlog of 120+ empty companies in the spreadsheet.
* **Solution**: We implemented the **Backlog Limit** in `agent.py`. Before finding new leads, the agent scans the Sheet. If there are >20 companies waiting for contact discovery, it skips Lead Generation entirely. This effectively throttles the expensive Gemini LLM calls to match the speed limit of the free-tier scraping APIs.

### 4. Git Push & Identity Hardcoding
* **Issue**: The codebase originally contained hardcoded resume data and hardcoded API tokens. When we wanted to push it to a public GitHub repo, this was a massive security and privacy risk.
* **Solution**: We refactored the entire system. We created a `.gitignore` to aggressively block tokens and `.env` files. We extracted the user's resume into a separate, ignored `profile.txt` file (with a public `profile.txt.example` provided for GitHub). Finally, we built `secure_init.py` to automatically scaffold the ignored `.creds/` directory, ensuring the repo was completely stateless and secure for open-source distribution.
