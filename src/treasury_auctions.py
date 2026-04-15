"""
Treasury investor class auction allotments — URL discovery and XLS parser.

Data source: https://home.treasury.gov/data/investor-class-auction-allotments
- Two series: Bills and Coupon Securities
- Files published monthly (~7th business day) as dated .xls files
- URL pattern: https://home.treasury.gov/system/files/276/<MonthName-Day-Year>-IC-Bills.xls
- Files are cumulative (contain all auctions from Oct 2009 to release date)
"""
import io
import logging
import re
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://home.treasury.gov"
_TREASURY_PAGE = "https://home.treasury.gov/data/investor-class-auction-allotments"

# data.gov CKAN API — more reliable than scraping the JS-rendered Treasury page
_CKAN_API = "https://catalog.data.gov/api/3/action/package_show"
_BILLS_PACKAGE  = "auction-allotments-by-investor-class-for-marketable-treasury-bill-securities"
_COUPONS_PACKAGE = "auction-allotments-by-investor-class-for-marketable-treasury-coupon-securities"

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


def _urls_from_ckan(package_id: str) -> list[str]:
    """Return all XLS/XLSX download URLs from a data.gov CKAN package."""
    try:
        resp = requests.get(_CKAN_API, params={"id": package_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            logger.warning("CKAN API returned success=false for package %s", package_id)
            return []
        urls = []
        for resource in data["result"].get("resources", []):
            url = resource.get("url", "")
            if re.search(r"\.xls[x]?$", url, re.IGNORECASE):
                urls.append(url)
        logger.info("CKAN: found %d XLS URLs for package %s", len(urls), package_id)
        return urls
    except Exception as exc:
        logger.warning("CKAN API failed for %s: %s", package_id, exc)
        return []


def _urls_from_html() -> dict[str, list[str]]:
    """Fallback: scrape the Treasury page for XLS links (works only if page is not JS-rendered)."""
    try:
        resp = requests.get(_TREASURY_PAGE, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        logger.warning("Treasury page fetch failed: %s", exc)
        return {"Bills": [], "Coupons": []}

    bills_urls, coupon_urls = [], []
    for href in re.findall(r'href="([^"]*-IC-(?:Bills|Coupons)[^"]*\.xls[x]?)"', html, re.IGNORECASE):
        full = href if href.startswith("http") else _BASE_URL + href
        if re.search(r"-IC-Bills", href, re.IGNORECASE):
            bills_urls.append(full)
        else:
            coupon_urls.append(full)

    logger.info("HTML scrape: found %d Bills, %d Coupon URLs", len(bills_urls), len(coupon_urls))
    return {"Bills": bills_urls, "Coupons": coupon_urls}


def discover_file_urls() -> dict[str, list[str]]:
    """
    Return all XLS download URLs for Bills and Coupon Securities.

    Tries data.gov CKAN API first (reliable JSON), falls back to HTML scraping.

    Returns:
        {"Bills": [url, ...], "Coupons": [url, ...]}  sorted for deterministic order
    """
    bills_urls  = _urls_from_ckan(_BILLS_PACKAGE)
    coupon_urls = _urls_from_ckan(_COUPONS_PACKAGE)

    # If CKAN found nothing, fall back to HTML scraping
    if not bills_urls and not coupon_urls:
        logger.warning("CKAN returned no URLs — falling back to HTML scraping")
        html_urls = _urls_from_html()
        bills_urls  = html_urls["Bills"]
        coupon_urls = html_urls["Coupons"]

    bills_deduped   = sorted(set(bills_urls))
    coupons_deduped = sorted(set(coupon_urls))

    logger.info("Final: %d Bills URLs, %d Coupon URLs", len(bills_deduped), len(coupons_deduped))
    for u in bills_deduped:
        logger.info("  Bills:   %s", u.split("/")[-1])
    for u in coupons_deduped:
        logger.info("  Coupons: %s", u.split("/")[-1])

    return {"Bills": bills_deduped, "Coupons": coupons_deduped}


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
