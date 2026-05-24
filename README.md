# 🤖 Outreach Agent — Setup Guide (Windows)

Gemini-powered cold outreach that runs daily, emails contacts, follows up, and updates your Google Sheet automatically.

---

## Prerequisites

- Python 3.10+
- Google Cloud project (already set up ✅)
- Gemini API key
- Gmail account you'll send from

---

## Step 1 — Enable APIs in Google Cloud Console

Go to https://console.cloud.google.com → your project → **APIs & Services** → **Enable APIs**

Enable these 3:
1. **Gmail API**
2. **Google Sheets API**
3. **Google Drive API**

---

## Step 2 — Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name it anything (e.g. "Outreach Agent")
5. Download the JSON → rename it to **`credentials.json`**
6. Place `credentials.json` in the `outreach_agent/` folder

---

## Step 3 — Copy your Google Sheet

1. Open the `job_search_tracker.xlsx` you downloaded
2. Go to Google Sheets → **File → Import** → upload the xlsx
3. Copy the Sheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/THIS_IS_YOUR_SHEET_ID/edit
   ```
4. Save it for the .env file

---

## Step 4 — Set Up Contact Discovery (APIs or Manual Workflow)

You now have a highly robust, prioritized multi-API auto-discovery engine supporting **Apollo.io**, **Snov.io**, and **Hunter.io** as fallbacks. 

You can choose either of the two workflows below:

### Option A: Fully Automated API Discovery (Apollo.io + Snov.io + Hunter.io)
If a company's "People Found" column (Column J) contains `""`, `"0"`, or `"0.0"`, the agent automatically queries your configured APIs in order:

1. **Apollo.io API** (Priority 1 — 50 free exports/month):
   - Go to [Apollo.io](https://app.apollo.io) and sign up for a free account.
   - Go to **Settings** → **Integrations** → **API Keys** → click **Create API Key**.
   - Add to your `.env` file: `APOLLO_API_KEY=your_created_key`

2. **Snov.io API** (Priority 2 — 50 free credits/month):
   - Go to [Snov.io](https://app.snov.io) and sign up for a free account.
   - Go to **Profile** → **API** → copy your **Client ID** and **Client Secret**.
   - Add to your `.env` file:
     ```env
     SNOV_CLIENT_ID=your_client_id
     SNOV_CLIENT_SECRET=your_client_secret
     ```

3. **Hunter.io API** (Priority 3 — 25 free searches/month):
   - Sign up at [Hunter.io](https://hunter.io). Go to **API** and copy your key.
   - Add to your `.env` file: `HUNTER_API_KEY=your_key`

---

### Option B: The High-Accuracy Manual Workflow (Recommended)
Free API tiers run out quickly. For maximum reliability, use the **Apollo.io Chrome Extension** manually:

1. Install the free **Apollo.io Chrome Extension** from the Chrome Web Store.
2. Go to LinkedIn, search for the target company's recruiters or engineering managers.
3. Open their profile, click the Apollo extension widget, and click **Show Email** (instantly reveals verified emails for free).
4. Paste the details directly into column J ("People Found") of your **🏢 Company Tracker** sheet in this exact format:
   ```text
   FirstName LastName|email@company.com|Job Title
   ```
   *(For multiple contacts at the same company, write them on separate lines inside the same cell)*
5. The agent will read this column daily and email each person individually.

---

## Step 5 — Install dependencies

```cmd
cd outreach_agent
pip install -r requirements.txt
```

---

## Step 6 — Configure .env

```cmd
copy .env.example .env
notepad .env
```

Fill in:
```
GEMINI_API_KEY=your_key_from_aistudio.google.com
SPREADSHEET_ID=your_sheet_id
SENDER_EMAIL=your.gmail@gmail.com
YOUR_NAME=Akshit Singh
EMAILS_PER_DAY=10
```

Load .env before running:
```cmd
:: Option A — PowerShell
Get-Content .env | ForEach-Object { if ($_ -match "^([^#][^=]*)=(.*)") { [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim()) } }

:: Option B — install python-dotenv (already in requirements.txt)
:: The scripts auto-load .env via dotenv
```

---

## Step 7 — First run (OAuth login)

```cmd
python agent.py
```

A browser window opens → log in with your Gmail → grant permissions.
A `token.pickle` file is saved. You won't need to log in again.

---

## Step 8 — Test run

```cmd
python scheduler.py --run-now
```

Check `agent.log` to verify emails sent and sheet updated.

---

## Step 9 — Run daily automatically (2 options)

### Option A: Keep terminal open (simple)
```cmd
python scheduler.py
```
Runs every day at 9:30 AM IST. Keep the window open or minimized.

### Option B: Windows Task Scheduler (runs even when terminal closed)

1. Press **Win + R** → type `taskschd.msc` → Enter
2. Click **Create Basic Task**
3. Name: `OutreachAgent`
4. Trigger: **Daily** → set time to **9:30 AM**
5. Action: **Start a program**
6. Program: `C:\path\to\outreach_agent\run_agent.bat`
7. Check **"Open Properties dialog"** → enable **"Run whether user is logged on or not"**
8. Click **OK** → enter your Windows password

Now it runs silently every morning without any terminal open.

---

## Daily workflow (what the agent does automatically)

```
9:30 AM every day:
1. Check Gmail inbox for replies → mark Replied? = Yes in sheet
2. Find contacts with no follow-up after 5 days → send follow-up email
3. Pick new contacts from "People Found" column in Company Tracker
4. Generate personalized hook via Gemini for each contact
5. Generate full email (recruiter style or EM advice style) via Gemini
6. Send via Gmail API (individual emails, never BCC)
7. Append row to Outreach Tracker with all details
8. Increment "Emails Sent" counter in Company Tracker
9. Log everything to agent.log
```

---

## Warm-up schedule (follow this to avoid spam flags)

| Days   | EMAILS_PER_DAY |
|--------|----------------|
| 1–3    | 5              |
| 4–7    | 10             |
| 8–14   | 20             |
| 15–30  | 30             |

Update `EMAILS_PER_DAY` in `.env` as you progress. To exhaust daily limits, scale `EMAILS_PER_DAY` up to `450` (near the 500 Gmail limit).

---

## 🤖 The Autonomous Lead Generation Agent (`lead_generator.py`)

A second autonomous agent has been added to automatically search, select, and compile target companies for your pipeline. 

### How it Works:
1. **Target Progression**: The agent operates on a global queue starting with Indian startup hubs (**Bengaluru, Hyderabad, Gurgaon, Pune**), transitioning to **Y Combinator** batches (e.g. YC Winter 2024, YC Summer 2023), and then progressing to global tech hubs (San Francisco, London, Berlin, Paris, Tokyo, etc.).
2. **State Memory**: It tracks its progress inside `lead_generator_state.json` so that each daily run automatically researches the next region in the queue without duplicating effort.
3. **Personalized Hooks**: It reads your `PROFILE_SUMMARY` and automatically drafts a hyper-personalized, one-sentence "Why Target" pitch in the spreadsheet.
4. **Sheet Integration**: It auto-populates the new companies into the **Company Tracker** Google Sheet with `"People Found"` set to `'0'` (telling the outreach agent that new contacts need to be discovered for these leads).
5. **Execution**:
   ```cmd
   python lead_generator.py
   ```

---

## ⚡ Gemini Rate-Limiting & Quota Management

The agent uses a premium robustness wrapper around the Gemini API:
* **Rate-Limit Respect (RPM)**: Enforces a mandatory **4-second sleep** between every Gemini API call to conform to the strict 15 RPM (Requests Per Minute) free tier quota.
* **Exponential Backoff**: If a `429 RESOURCE_EXHAUSTED` (Rate Limit Exceeded) error occurs under heavy load, the agent dynamically increases sleep time (`20s`, `40s`, `60s`) and retries up to 4 times, ensuring your daily campaign runs complete successfully.

---

## File structure

```
outreach_agent/
├── agent.py               ← main Gemini outreach agent logic (sends emails)
├── lead_generator.py      ← autonomous startup/lead research agent (finds leads)
├── contact_finder.py      ← multi-API contact discoverer (Apollo + Snov + Hunter)
├── sheets.py              ← Google Sheets read/write manager with error handles
├── mailer.py              ← Gmail API sender and reply-sync logic
├── scheduler.py           ← daily scheduler (APScheduler)
├── run_agent.bat          ← Windows Task Scheduler trigger
├── requirements.txt
├── .env                   ← your secrets and API keys (never commit)
├── .env.example           ← template configuration
├── credentials.json       ← OAuth credentials (never commit)
├── token.pickle           ← auto-generated OAuth token after login
├── lead_generator_state.json ← state tracker for the lead gen queue
├── lead_generator.log     ← lead generator run logs
└── agent.log              ← daily outreach run logs
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `GEMINI_API_KEY not set` | Check .env is loaded, run PowerShell command in Step 6 |
| `credentials.json not found` | Re-download from Google Cloud Console, place in folder |
| `Token has been expired` | Delete `token.pickle`, run `python agent.py` to re-auth |
| `No contacts found for X` | Add contacts to "People Found" col J in Company Tracker |
| `Gmail quota exceeded` | Reduce `EMAILS_PER_DAY`, wait 24hrs |
| `Gemini rate limit` | Already handled with retries — check agent.log for details |

---

## Getting Gemini API key

1. Go to https://aistudio.google.com/app/apikey
2. Click **Create API key**
3. Copy into `.env` as `GEMINI_API_KEY=...`

Free tier: 15 requests/min, 1500/day — more than enough.

---

## ⚠️ Important

- Never commit `.env` or `credentials.json` to git
- Add both to `.gitignore`
- The agent sends real emails from your Gmail — test with `--run-now` first
- Start with `EMAILS_PER_DAY=5` for first 3 days (inbox warm-up)
