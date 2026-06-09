"""
Backfill Treasury investor class auction allotments into Turso.

Downloads all available Bills and Coupon Securities XLS files from
https://home.treasury.gov/data/investor-class-auction-allotments

Usage:
    python scripts/backfill_auctions.py                   # all available data
    python scripts/backfill_auctions.py --from 2020-01-01 # only load auctions on/after date
    python scripts/backfill_auctions.py --resume          # only load files with data newer than DB

Requires env vars: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
"""
import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.treasury_auctions import discover_and_download, parse_xls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SLEEP_BETWEEN = 1.0   # seconds between requests
SLEEP_ON_ERROR = 5.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Treasury auction allotments into Turso")
    parser.add_argument("--from", dest="from_date", help="Only load auctions with issue_date >= YYYY-MM-DD")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip files that only contain data already in the database",
    )
    args = parser.parse_args()

    db.init_auction_db()

    from_date = None
    if args.resume:
        latest = db.get_auction_latest_issue_date()
        if latest:
            from_date = latest
            logger.info("Resuming — will skip auctions on or before %s", from_date)
        else:
            logger.info("Database is empty — loading all available data")
    elif args.from_date:
        from_date = args.from_date
        logger.info("Loading auctions with issue_date >= %s", from_date)

    logger.info("Discovering and downloading files from Treasury website…")
    try:
        files = discover_and_download()
    except Exception as exc:
        logger.error("Failed to fetch files: %s", exc)
        sys.exit(1)

    total_files = sum(len(v) for v in files.values())
    if total_files == 0:
        logger.warning("No files found. Treasury may not have published this month's data yet.")
        sys.exit(1)

    success = skipped = failed = 0
    total_records = 0
    file_num = 0

    for series, file_list in files.items():
        for url, content in file_list:
            file_num += 1
            fname = url.split("/")[-1]
            try:
                records = parse_xls(content, series)

                if from_date:
                    records = [r for r in records if r["issue_date"] > from_date]

                if not records:
                    skipped += 1
                    logger.info("[%d/%d] %s  skipped (no new data after %s)", file_num, total_files, fname, from_date)
                    continue

                db.upsert_auction_records(records)
                total_records += len(records)
                success += 1
                logger.info("[%d/%d] %s  ✓  %d records", file_num, total_files, fname, len(records))

            except KeyboardInterrupt:
                logger.warning("Interrupted after %d files.", file_num - 1)
                break
            except Exception as exc:
                failed += 1
                logger.error("[%d/%d] %s  ✗  %s", file_num, total_files, fname, exc)
                time.sleep(SLEEP_ON_ERROR)

    logger.info(
        "Done.  Files — Success: %d  |  Skipped: %d  |  Failed: %d  |  Total records: %d",
        success, skipped, failed, total_records,
    )
    if failed:
        logger.warning("%d files failed.", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
