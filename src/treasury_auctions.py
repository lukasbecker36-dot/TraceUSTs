"""
Treasury investor class auction allotments — URL discovery and XLS parser.

Data source: https://home.treasury.gov/data/investor-class-auction-allotments
- Two series: Bills and Coupon Securities
- Files published monthly (~7th business day at 3 PM ET) as dated .xls files
- URL patterns (Treasury has used both separators):
    https://home.treasury.gov/system/files/276/June_8_2026_IC_Bills.xls   (underscore, newer)
    https://home.treasury.gov/system/files/276/March-9-2026-IC-Bills.xls  (hyphen, older)
- Files are cumulative (each file contains all auctions from Oct 2009 to release date)
"""
import calendar
import io
import logging
import re
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE = "https://home.treasury.gov/system/files/276"

# Columns that identify an auction row (not investor class allotment columns)
_ID_COL_PATTERNS = [
    r"issue\s*date",
    r"security\s*type",
    r"cusip",
    r"maturity\s*date",
    r"coupon",
    r"auction\s*high\s*rate",
    r"term",
    r"total\s*issue\s*(amount|amt)",
    r"re-?opening",
]


def _is_id_col(name: str) -> bool:
    n = str(name).lower().strip()
    return any(re.search(p, n) for p in _ID_COL_PATTERNS)


def _candidate_urls(year: int, month: int, day: int, series: str) -> list[str]:
    """Both known URL formats — Treasury switched from hyphens to underscores at some point."""
    m = calendar.month_name[month]
    bills = series == "Bills"
    return [
        f"{_BASE}/{m}_{day}_{year}_IC_{'Bills' if bills else 'Coupons'}.xls",   # newer: Jun_8_2026
        f"{_BASE}/{m}-{day}-{year}-IC-{'Bills' if bills else 'Coupons'}.xls",  # older: Mar-9-2026
    ]


def _find_url_for_month(year: int, month: int, series: str) -> Optional[str]:
    """
    Probe days 5–20 of the given month, trying both URL formats per day.
    The 7th business day typically falls between days 8–15.
    """
    today = date.today()
    for day in range(5, 21):
        try:
            if date(year, month, day) > today:
                break
        except ValueError:
            continue
        for url in _candidate_urls(year, month, day, series):
            try:
                resp = requests.head(url, timeout=10, allow_redirects=True)
                if resp.status_code == 200:
                    logger.info("Found %s: %s", series, url.split("/")[-1])
                    return url
            except requests.RequestException:
                continue
    return None


def discover_file_urls() -> dict[str, list[str]]:
    """
    Probe the Treasury CDN to find the most recently published Bills and Coupons files.

    Checks the current month and 3 prior months (covers the case where the current
    month's file hasn't been published yet).

    Returns:
        {"Bills": [url], "Coupons": [url]}  — typically one URL per series
        (files are cumulative so the latest file contains all historical data)
    """
    today = date.today()
    bills_urls: list[str] = []
    coupon_urls: list[str] = []

    # Walk back through recent months until we find a file for each series
    for months_back in range(4):
        year = today.year
        month = today.month - months_back
        while month <= 0:
            month += 12
            year -= 1

        if not bills_urls:
            url = _find_url_for_month(year, month, "Bills")
            if url:
                bills_urls.append(url)

        if not coupon_urls:
            url = _find_url_for_month(year, month, "Coupons")
            if url:
                coupon_urls.append(url)

        if bills_urls and coupon_urls:
            break

    logger.info("Final: %d Bills URLs, %d Coupon URLs", len(bills_urls), len(coupon_urls))
    for u in bills_urls:
        logger.info("  Bills:   %s", u.split("/")[-1])
    for u in coupon_urls:
        logger.info("  Coupons: %s", u.split("/")[-1])

    return {"Bills": bills_urls, "Coupons": coupon_urls}


def download_xls(url: str) -> Optional[bytes]:
    """Download an XLS file. Returns None on 404 or other HTTP error."""
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            logger.warning("Not found: %s", url)
            return None
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        logger.error("Download failed for %s: %s", url, exc)
        return None


def _find_header_row(raw: pd.DataFrame) -> int:
    """
    Find the row index that contains the column headers.
    Looks for a row where one cell contains 'Issue Date' or 'CUSIP'.
    """
    for i, row in raw.iterrows():
        for cell in row:
            val = str(cell).strip().lower()
            if val in ("issue date", "cusip"):
                return int(i)
    return 0


def _normalise_date(val) -> Optional[str]:
    """Convert Excel date serial or string to ISO YYYY-MM-DD, or None."""
    if val is None or (isinstance(val, float) and val != val):
        return None
    try:
        return pd.to_datetime(val).strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_xls(content: bytes, series: str) -> list[dict]:
    """
    Parse a Treasury investor class allotments XLS file.

    Args:
        content: Raw bytes of the .xls file
        series: 'Bills' or 'Coupons'

    Returns:
        List of dicts ready for db.upsert_auction_records()
    """
    buf = io.BytesIO(content)

    # First pass: find header row
    try:
        raw = pd.read_excel(buf, engine="xlrd", header=None)
    except Exception as exc:
        logger.error("Failed to read XLS: %s", exc)
        return []

    header_row = _find_header_row(raw)
    buf.seek(0)

    # Second pass: read with correct header
    try:
        df = pd.read_excel(buf, engine="xlrd", header=header_row)
    except Exception as exc:
        logger.error("Failed to re-read XLS with header row %d: %s", header_row, exc)
        return []

    # Normalise column names
    df.columns = [str(c).strip() for c in df.columns]

    # Identify identifier columns vs investor class columns
    id_cols = [c for c in df.columns if _is_id_col(c)]
    investor_cols = [c for c in df.columns if not _is_id_col(c) and not str(c).startswith("Unnamed")]

    if not investor_cols:
        logger.warning("No investor class columns found in %s file; columns: %s", series, list(df.columns))
        return []

    # Map known identifier column names
    def _find_col(patterns: list[str]) -> Optional[str]:
        for col in df.columns:
            for p in patterns:
                if re.search(p, col.lower()):
                    return col
        return None

    col_issue_date = _find_col([r"issue\s*date"])
    col_security_type = _find_col([r"security\s*type"])
    col_cusip = _find_col([r"cusip"])
    col_maturity = _find_col([r"maturity\s*date"])
    col_rate = _find_col([r"coupon", r"auction\s*high\s*rate"])
    col_total = _find_col([r"total\s*issue\s*(amount|amt)"])

    if not col_issue_date or not col_cusip:
        logger.warning("Missing Issue Date or CUSIP column in %s file", series)
        return []

    records = []
    for _, row in df.iterrows():
        cusip = str(row.get(col_cusip, "")).strip()
        if not cusip or cusip.lower() in ("nan", "cusip", "total", ""):
            continue

        issue_date = _normalise_date(row.get(col_issue_date))
        if not issue_date:
            continue

        security_type = str(row.get(col_security_type, "")).strip() if col_security_type else ""
        maturity_date = _normalise_date(row.get(col_maturity)) if col_maturity else None
        rate_val = row.get(col_rate) if col_rate else None
        try:
            rate = float(rate_val) if rate_val is not None and str(rate_val).strip() not in ("", "nan") else None
        except (ValueError, TypeError):
            rate = None

        total_val = row.get(col_total) if col_total else None
        try:
            total_issue_amt = float(total_val) if total_val is not None and str(total_val).strip() not in ("", "nan") else None
        except (ValueError, TypeError):
            total_issue_amt = None

        for inv_class in investor_cols:
            raw_val = row.get(inv_class)
            try:
                allotment = float(raw_val) if raw_val is not None and str(raw_val).strip() not in ("", "nan") else None
            except (ValueError, TypeError):
                allotment = None

            records.append({
                "issue_date": issue_date,
                "series": series,
                "security_type": security_type,
                "cusip": cusip,
                "maturity_date": maturity_date,
                "rate": rate,
                "total_issue_amt": total_issue_amt,
                "investor_class": inv_class.strip(),
                "allotment_amt": allotment,
            })

    logger.info("Parsed %d records from %s XLS (%d auctions)", len(records), series, len(records) // max(len(investor_cols), 1))
    return records


def fetch_and_parse_url(url: str, series: str) -> Optional[list[dict]]:
    """Download and parse a single XLS URL. Returns None on download failure."""
    content = download_xls(url)
    if content is None:
        return None
    return parse_xls(content, series)
