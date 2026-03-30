"""
FINRA TRACE Treasury daily aggregate downloader and XLSX parser.

CDN URL pattern:
  https://cdn.finra.org/trace/treasury-aggregates/daily/ts-daily-aggregates-YYYY-MM-DD.xlsx

The parser is intentionally flexible: it normalises column names at runtime
so it adapts if FINRA ever renames headers.  Run scripts/inspect_xlsx.py once
to print the actual column names from a live file.
"""
import io
import logging
from datetime import date
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

CDN_BASE = "https://cdn.finra.org/trace/treasury-aggregates/daily"
CDN_URL = f"{CDN_BASE}/ts-daily-aggregates-{{date}}.xlsx"

# ── Column name normalisation ─────────────────────────────────────────────────
# Maps lowercase-stripped column headers → our internal names.
# Extend here if FINRA renames a column.
_COL_MAP: dict[str, str] = {
    # Date
    "trade date": "trade_date",
    "date": "trade_date",
    # Security subtype
    "security subtype": "security_subtype",
    "security type": "security_subtype",
    "subtype": "security_subtype",
    "instrument type": "security_subtype",
    # Maturity bucket
    "remaining maturity": "maturity_bucket",
    "remaining maturity range": "maturity_bucket",
    "maturity range": "maturity_bucket",
    "maturity": "maturity_bucket",
    # On-the-run
    "on-the-run/off-the-run": "on_the_run_raw",
    "on/off the run": "on_the_run_raw",
    "on the run indicator": "on_the_run_raw",
    "on-the-run indicator": "on_the_run_raw",
    "run status": "on_the_run_raw",
    # Trading category
    "trade category": "trading_category",
    "trading category": "trading_category",
    "category": "trading_category",
    "trade type": "trading_category",
    # Volume
    "par value (billions)": "volume_par",
    "par amount (billions)": "volume_par",
    "volume (par, billions)": "volume_par",
    "volume (billions)": "volume_par",
    "par value": "volume_par",
    "volume": "volume_par",
    "total par (billions)": "volume_par",
    # Trade count
    "trade count": "trade_count",
    "number of trades": "trade_count",
    "# of trades": "trade_count",
    "# trades": "trade_count",
    "count": "trade_count",
    # VWAP
    "vwap": "vwap",
    "volume weighted average price": "vwap",
    "weighted average price": "vwap",
    "avg price": "vwap",
}

# Normalised on-the-run text → stored value ('On' / 'Off')
_OTR_MAP: dict[str, str] = {
    "on-the-run": "On",
    "on the run": "On",
    "on": "On",
    "otr": "On",
    "off-the-run": "Off",
    "off the run": "Off",
    "off": "Off",
    "ofr": "Off",
}


# ── Download ──────────────────────────────────────────────────────────────────

def build_url(trade_date: date) -> str:
    return CDN_URL.format(date=trade_date.strftime("%Y-%m-%d"))


def download_xlsx(trade_date: date) -> Optional[bytes]:
    """Return raw XLSX bytes for trade_date, or None if the file doesn't exist."""
    url = build_url(trade_date)
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            logger.warning("No file for %s (HTTP 404)", trade_date)
            return None
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        logger.error("Download failed for %s: %s", trade_date, exc)
        raise


# ── Parse ─────────────────────────────────────────────────────────────────────

def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns using _COL_MAP (case/whitespace insensitive)."""
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in _COL_MAP:
            rename[col] = _COL_MAP[key]
    if rename:
        df = df.rename(columns=rename)
    return df


def parse_xlsx(content: bytes, trade_date: date) -> list[dict]:
    """
    Parse XLSX content into a list of record dicts ready for db.upsert_records().

    If the actual column names don't match any entry in _COL_MAP, extend that
    dict.  Run scripts/inspect_xlsx.py to print the live column names.
    """
    xls = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    sheet = xls.sheet_names[0]
    df = xls.parse(sheet)

    logger.debug("Raw columns in %s sheet '%s': %s", trade_date, sheet, list(df.columns))

    df = _normalise_columns(df)
    df = df.dropna(how="all")

    date_str = trade_date.strftime("%Y-%m-%d")
    records: list[dict] = []

    for _, row in df.iterrows():
        # ── Required fields ────────────────────────────────────────────────
        subtype = _str_or_none(row, "security_subtype")
        category = _str_or_none(row, "trading_category")
        if not subtype or not category:
            continue  # skip footer / total rows without key identifiers

        # ── Optional fields ────────────────────────────────────────────────
        maturity = _str_or_none(row, "maturity_bucket") or ""
        otr_raw = _str_or_none(row, "on_the_run_raw")
        on_the_run = _OTR_MAP.get(otr_raw.lower(), "") if otr_raw else ""

        volume = _float_or_none(row, "volume_par")
        count = _int_or_none(row, "trade_count")
        vwap = _float_or_none(row, "vwap")

        records.append({
            "trade_date": date_str,
            "security_subtype": subtype,
            "trading_category": category,
            "maturity_bucket": maturity,
            "on_the_run": on_the_run,
            "volume_par": volume,
            "trade_count": count,
            "vwap": vwap,
        })

    logger.info("Parsed %d records for %s", len(records), trade_date)
    return records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _str_or_none(row: pd.Series, col: str) -> Optional[str]:
    val = row.get(col)
    if val is None or (isinstance(val, float) and val != val):
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("nan", "none", "n/a", "-") else None


def _float_or_none(row: pd.Series, col: str) -> Optional[float]:
    val = row.get(col)
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f  # NaN check
    except (ValueError, TypeError):
        return None


def _int_or_none(row: pd.Series, col: str) -> Optional[int]:
    val = _float_or_none(row, col)
    return int(val) if val is not None else None


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_day(trade_date: date) -> Optional[list[dict]]:
    """Download and parse one trading day.  Returns None if no data available."""
    content = download_xlsx(trade_date)
    if content is None:
        return None
    return parse_xlsx(content, trade_date)
