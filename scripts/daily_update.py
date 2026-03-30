"""
Daily update script — run by GitHub Actions each evening.

Fetches the previous business day's data from the FINRA CDN and upserts
it into Turso.  Exits with code 1 on failure so the Actions run turns red.

Requires env vars: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
"""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, finra  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def last_business_day() -> date:
    """Return the most recent weekday before today."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main() -> None:
    db.init_db()

    target = last_business_day()
    logger.info("Fetching data for %s", target)

    try:
        records = finra.fetch_day(target)
    except Exception as exc:
        logger.error("Download error for %s: %s", target, exc)
        sys.exit(1)

    if records is None:
        logger.info(
            "No data for %s — likely a market holiday or data not yet published.", target
        )
        return

    inserted = db.upsert_records(records)
    logger.info("Upserted %d records for %s", inserted, target)


if __name__ == "__main__":
    main()
