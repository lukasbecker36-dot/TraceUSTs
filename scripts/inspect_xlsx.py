"""
Utility: download the latest (or a specific) daily file and print its structure.

Run this once after the CDN becomes accessible to confirm column names match
the parser's _COL_MAP.  No database writes are performed.

Usage:
    python scripts/inspect_xlsx.py                    # uses yesterday
    python scripts/inspect_xlsx.py --date 2026-03-27
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.finra import build_url, download_xlsx  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a FINRA TRACE treasury XLSX file")
    parser.add_argument("--date", help="Trade date (YYYY-MM-DD). Defaults to yesterday.")
    args = parser.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today() - timedelta(days=1)
        while target.weekday() >= 5:
            target -= timedelta(days=1)

    print(f"\nURL:  {build_url(target)}")
    print(f"Date: {target}\n")

    content = download_xlsx(target)
    if content is None:
        print("No file found (HTTP 404). Try a different date.")
        sys.exit(1)

    import io
    xls = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    print(f"Sheets: {xls.sheet_names}\n")

    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        print(f"── Sheet: '{sheet}'  ({len(df)} rows × {len(df.columns)} cols) ──")
        print("Columns:")
        for col in df.columns:
            sample = df[col].dropna().iloc[:3].tolist() if not df[col].dropna().empty else []
            print(f"  {col!r:45s}  sample: {sample}")
        print()
        print(df.head(10).to_string())
        print()


if __name__ == "__main__":
    main()
