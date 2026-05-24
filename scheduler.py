"""
scheduler.py — Runs the outreach agent daily for 30 days
Windows-compatible using APScheduler.

Usage:
    python scheduler.py              # runs at 9:30 AM IST daily for 30 days
    python scheduler.py --run-now    # run immediately once (for testing)
"""

from dotenv import load_dotenv

load_dotenv()
import sys
import logging
import argparse
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from agent import OutreachAgent

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("scheduler.log"), logging.StreamHandler()],
)

START_DATE = datetime.now().date()
END_DATE = START_DATE + timedelta(days=30)
RUN_HOUR = 18  # 6 PM IST
RUN_MINUTE = 0

run_count = {"days": 0}
scheduler = None  # will be set in __main__


def daily_job():
    today = datetime.now().date()
    if today > END_DATE:
        log.info("30-day campaign complete. Shutting down scheduler.")
        if scheduler:
            scheduler.shutdown(wait=False)
        return

    run_count["days"] += 1
    log.info(f"=== Day {run_count['days']}/30 — {today} ===")

    try:
        agent = OutreachAgent()
        agent.run_daily()
    except Exception as e:
        log.error(f"Agent run failed: {e}", exc_info=True)

    log.info(
        f"=== Day {run_count['days']} done. Next run: tomorrow {RUN_HOUR:02d}:{RUN_MINUTE:02d} IST ==="
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-now", action="store_true", help="Run immediately once (test mode)"
    )
    args = parser.parse_args()

    if args.run_now:
        log.info("Running immediately (test mode)...")
        daily_job()
        sys.exit(0)

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        daily_job,
        trigger=CronTrigger(hour=RUN_HOUR, minute=RUN_MINUTE),
        id="daily_outreach",
        name="Daily Cold Outreach",
        misfire_grace_time=3600,  # run even if PC was off
    )

    log.info(f"Scheduler started. Runs daily at {RUN_HOUR:02d}:{RUN_MINUTE:02d} IST")
    log.info(f"Campaign: {START_DATE} -> {END_DATE} (30 days)")
    log.info(
        "Keep this terminal open, or use Windows Task Scheduler (see README / setup.bat)."
    )
    log.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
