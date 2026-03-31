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
from src.treasury_auctions import discover_file_urls, fetch_and_parse_url

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

    logger.info("Discovering available files from Treasury website…")
    try:
        urls_by_series = discover_file_urls()
    except Exception as exc:
        logger.error("Failed to discover URLs: %s", exc)
        sys.exit(1)

    total_urls = sum(len(v) for v in urls_by_series.values())
    logger.info("Found %d files total (%d Bills, %d Coupons)",
                total_urls,
                len(urls_by_series.get("Bills", [])),
                len(urls_by_series.get("Coupons", [])))

    if total_urls == 0:
        logger.warning("No files found. Check that the Treasury page is accessible.")
        sys.exit(1)

    success = skipped = failed = 0
    total_records = 0
    file_num = 0

    for series, urls in urls_by_series.items():
        for url in urls:
            file_num += 1
            try:
                records = fetch_and_parse_url(url, series)
                if records is None:
                    skipped += 1
                    logger.info("[%d/%d] %s  skipped (download failed)", file_num, total_urls, url.split("/")[-1])
                    time.sleep(SLEEP_BETWEEN)
                    continue

                # Filter by from_date if set
                if from_date:
                    records = [r for r in records if r["issue_date"] > from_date]

                if not records:
                    skipped += 1
                    logger.info("[%d/%d] %s  skipped (no new data after %s)",
                                file_num, total_urls, url.split("/")[-1], from_date)
                    time.sleep(SLEEP_BETWEEN)
                    continue

                db.upsert_auction_records(records)
                total_records += len(records)
                success += 1
                logger.info("[%d/%d] %s  ✓  %d records",
                            file_num, total_urls, url.split("/")[-1], len(records))
                time.sleep(SLEEP_BETWEEN)

            except KeyboardInterrupt:
                logger.warning("Interrupted after %d files.", file_num - 1)
                break
            except Exception as exc:
                failed += 1
                logger.error("[%d/%d] %s  ✗  %s", file_num, total_urls, url.split("/")[-1], exc)
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
