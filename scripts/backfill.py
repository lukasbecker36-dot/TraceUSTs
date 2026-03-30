"""
One-time historical backfill: downloads all available daily files from
START_DATE to yesterday and inserts them into Turso.

Usage:
    python scripts/backfill.py                  # start from START_DATE
    python scripts/backfill.py --from 2023-01-01  # start from a specific date
    python scripts/backfill.py --resume           # start from day after latest DB entry

Requires env vars: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
"""
import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, finra  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# FINRA daily aggregate data became available from this date.
# Files before this date will return 404 and be skipped.
START_DATE = date(2020, 1, 1)

# Seconds to sleep between requests (be polite to the CDN)
SLEEP_BETWEEN = 0.5
SLEEP_ON_ERROR = 5


def weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon–Fri
            yield current
        current += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill FINRA treasury data into Turso")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Start from the day after the latest date already in the database",
    )
    args = parser.parse_args()

    db.init_db()

    if args.resume:
        latest = db.get_latest_date()
        if latest:
            start = date.fromisoformat(latest) + timedelta(days=1)
            logger.info("Resuming from %s (latest in DB: %s)", start, latest)
        else:
            start = START_DATE
            logger.info("Database is empty — starting from %s", start)
    elif args.from_date:
        start = date.fromisoformat(args.from_date)
        logger.info("Starting from %s (user-supplied)", start)
    else:
        latest = db.get_latest_date()
        if latest:
            start = date.fromisoformat(latest) + timedelta(days=1)
            logger.info("Resuming from %s (latest in DB: %s)", start, latest)
        else:
            start = START_DATE
            logger.info("Starting fresh from %s", start)

    end = date.today() - timedelta(days=1)

    if start > end:
        logger.info("Database is already up to date (latest: %s). Nothing to do.", end)
        return

    days = list(weekdays(start, end))
    total = len(days)
    logger.info("Fetching %d weekdays from %s → %s", total, start, end)

    success = skipped = failed = 0

    for i, d in enumerate(days, 1):
        try:
            records = finra.fetch_day(d)
            if records is None:
                skipped += 1
                logger.info("[%d/%d] %s  skipped (no file / market holiday)", i, total, d)
            else:
                db.upsert_records(records)
                success += 1
                logger.info("[%d/%d] %s  ✓  %d records", i, total, d, len(records))
            time.sleep(SLEEP_BETWEEN)

        except KeyboardInterrupt:
            logger.warning("Interrupted by user after %d days.", i - 1)
            break

        except Exception as exc:
            failed += 1
            logger.error("[%d/%d] %s  ✗  %s", i, total, d, exc)
            time.sleep(SLEEP_ON_ERROR)

    logger.info(
        "Done.  Success: %d  |  Skipped (no data): %d  |  Failed: %d",
        success, skipped, failed,
    )
    if failed:
        logger.warning("%d days failed — re-run with --resume to retry.", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
