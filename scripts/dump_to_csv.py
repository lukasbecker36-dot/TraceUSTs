"""
Dump all Turso tables to CSV files in data/.

Fetches in pages of PAGE_SIZE rows to stay within Turso HTTP response limits.
Writes:
    data/treasury_daily.csv
    data/auction_allotments.csv

Usage:
    python scripts/dump_to_csv.py
    python scripts/dump_to_csv.py --table treasury_daily
    python scripts/dump_to_csv.py --table auction_allotments

Requires env vars: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
"""
import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PAGE_SIZE = 5_000

TABLES = {
    "treasury_daily": {
        "order_by": "trade_date, security_subtype, trading_category, maturity_bucket, on_the_run",
        "init": db.init_db,
    },
    "auction_allotments": {
        "order_by": "issue_date, series, cusip, investor_class",
        "init": db.init_auction_db,
    },
}


def dump_table(table: str, out_path: Path) -> int:
    order_by = TABLES[table]["order_by"]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    offset = 0
    total = 0
    writer = None
    f = None

    try:
        while True:
            sql = f"SELECT * FROM {table} ORDER BY {order_by} LIMIT {PAGE_SIZE} OFFSET {offset}"
            result = db.execute(sql)

            if not result.get("cols"):
                break

            cols = [c["name"] for c in result["cols"]]
            rows = result.get("rows", [])

            if not rows:
                break

            # Open file on first page (write mode); subsequent pages append
            if writer is None:
                f = open(out_path, "w", newline="", encoding="utf-8")
                writer = csv.writer(f)
                writer.writerow(cols)

            for row in rows:
                writer.writerow([
                    v.get("value") if v["type"] != "null" else ""
                    for v in row
                ])

            total += len(rows)
            logger.info("%s: fetched rows %d–%d", table, offset, offset + len(rows) - 1)

            if len(rows) < PAGE_SIZE:
                break  # last page

            offset += PAGE_SIZE

    finally:
        if f:
            f.close()

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump Turso tables to CSV")
    parser.add_argument(
        "--table",
        choices=list(TABLES.keys()),
        help="Dump only this table (default: all tables)",
    )
    args = parser.parse_args()

    tables_to_dump = [args.table] if args.table else list(TABLES.keys())

    for table in tables_to_dump:
        TABLES[table]["init"]()
        out_path = Path("data") / f"{table}.csv"
        logger.info("Dumping %s → %s", table, out_path)
        try:
            n = dump_table(table, out_path)
            logger.info("Done: %d rows written to %s", n, out_path)
        except Exception as exc:
            logger.error("Failed to dump %s: %s", table, exc)
            sys.exit(1)


if __name__ == "__main__":
    main()
