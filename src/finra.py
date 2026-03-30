"""
FINRA TRACE Treasury daily aggregate downloader and XLSX parser.

CDN URL:
  https://cdn.finra.org/trace/treasury-aggregates/daily/ts-daily-aggregates-YYYY-MM-DD.xlsx

File layout (confirmed from live file 2026-03-27):
  Row 0  : Title  "TRACE Volumes - Month DD, YYYY"  (merged across all columns)
  Row 1  : Group headers — "Category" | "ATS & Interdealer" (×2) | "Dealer to Customer" (×2) | "Total" (×2) | "VWAP"
  Row 2  : Sub-headers  — ""         | "Trades" | "Par Value" | "Trades" | "Par Value" | "Trades" | "Par Value" | ""
  Row 3+ : Data

The "Category" column encodes a 3-level hierarchy (no indentation in cell text):
  Bills
  FRNs
  Nominal Coupons          ← subtype aggregate (all maturities combined)
    <= 2 years             ← maturity aggregate (OTR + OFR combined)
      On-the-run
      Off-the-run
    > 2 years and <= 3 years
      On-the-run
      Off-the-run
    ...
  TIPS
    <= 5 years
      On-the-run
      Off-the-run
    ...
  Total                    ← grand total (skipped; derivable)

Each XLSX row is pivoted to 3 DB records — one per trading category
(ATS and Interdealer / Dealer-to-Customer / Total).
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

# ── Category classification ───────────────────────────────────────────────────

# Maps raw Category text → normalised security_subtype stored in DB
_SUBTYPE_MARKERS: dict[str, str] = {
    "Bills": "Bills",
    "FRNs": "FRN",
    "Nominal Coupons": "Nominal Coupons",
    "TIPS": "TIPS",
}

# Maps raw Category text → on_the_run value stored in DB
_OTR_MARKERS: dict[str, str] = {
    "On-the-run": "On",
    "Off-the-run": "Off",
}

# Rows whose Category text means "skip this row entirely"
_SKIP_MARKERS = {"Total", "Notes"}

# ── Flat column names after parsing the wide header ───────────────────────────
# Layout: Category | ATS Trades | ATS Par | D2C Trades | D2C Par | Total Trades | Total Par | VWAP
_FLAT_COLS = [
    "category",
    "ats_trades", "ats_par",
    "d2c_trades", "d2c_par",
    "total_trades", "total_par",
    "vwap",
]

# Maps (trades_col, par_col) → trading_category label stored in DB
_CATEGORY_COLS = [
    ("ats_trades",   "ats_par",   "ATS and Interdealer"),
    ("d2c_trades",   "d2c_par",   "Dealer-to-Customer"),
    ("total_trades", "total_par", "Total"),
]


# ── Download ──────────────────────────────────────────────────────────────────

def build_url(trade_date: date) -> str:
    return CDN_URL.format(date=trade_date.strftime("%Y-%m-%d"))


def download_xlsx(trade_date: date) -> Optional[bytes]:
    """Return raw XLSX bytes, or None if the file doesn't exist (HTTP 404)."""
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

def parse_xlsx(content: bytes, trade_date: date) -> list[dict]:
    """
    Parse a TRACE Treasury daily XLSX into DB-ready records.

    Strategy:
    1. Read without header; find the first data row by locating the first row
       whose column-0 value is a known security subtype (e.g. "Bills").
    2. Assign flat column names based on the known 8-column layout.
    3. Walk the Category column with a state machine to infer subtype /
       maturity / on-the-run for each row.
    4. Pivot each wide row to 3 records (ATS, D2C, Total).
    """
    raw = pd.read_excel(io.BytesIO(content), engine="openpyxl", header=None)

    # ── Find the first data row ───────────────────────────────────────────────
    data_start = None
    for idx, row in raw.iterrows():
        cell = str(row.iloc[0]).strip()
        if cell in _SUBTYPE_MARKERS:
            data_start = idx
            break

    if data_start is None:
        logger.error("Could not locate data rows in XLSX for %s — no known security type found", trade_date)
        return []

    data = raw.iloc[data_start:].copy().reset_index(drop=True)

    # Trim to the expected number of columns
    n_cols = min(len(_FLAT_COLS), len(data.columns))
    data = data.iloc[:, :n_cols].copy()
    data.columns = _FLAT_COLS[:n_cols]

    # Clean up the Category column
    data["category"] = data["category"].astype(str).str.strip()

    date_str = trade_date.strftime("%Y-%m-%d")
    records: list[dict] = []

    current_subtype: Optional[str] = None
    current_maturity: Optional[str] = None   # empty string = no maturity breakdown

    for _, row in data.iterrows():
        cat = row["category"]

        # ── Skip non-data rows ────────────────────────────────────────────────
        if not cat or cat.lower() in ("nan", "none") or cat in _SKIP_MARKERS:
            continue
        if cat.lower().startswith("strips"):
            continue  # footnote row

        # ── Classify the row ──────────────────────────────────────────────────
        if cat in _SUBTYPE_MARKERS:
            # Security-subtype aggregate row (all maturities combined)
            current_subtype = _SUBTYPE_MARKERS[cat]
            current_maturity = ""
            maturity = ""
            on_the_run = ""

        elif cat in _OTR_MARKERS:
            # On-the-run / Off-the-run sub-row
            if current_subtype is None:
                continue
            maturity = current_maturity or ""
            on_the_run = _OTR_MARKERS[cat]

        elif "year" in cat.lower():
            # Maturity-bucket aggregate row (OTR + OFR combined)
            if current_subtype is None:
                continue
            current_maturity = cat
            maturity = cat
            on_the_run = ""

        else:
            logger.debug("Unrecognised category row '%s' — skipping", cat)
            continue

        # ── Emit one record per trading category ──────────────────────────────
        vwap_val = _float_val(row, "vwap") if "vwap" in row.index else None

        for trades_col, par_col, category_label in _CATEGORY_COLS:
            records.append({
                "trade_date":       date_str,
                "security_subtype": current_subtype,
                "trading_category": category_label,
                "maturity_bucket":  maturity,
                "on_the_run":       on_the_run,
                "volume_par":       _float_val(row, par_col),
                "trade_count":      _int_val(row, trades_col),
                # VWAP only applies to the Total category (rightmost column)
                "vwap": vwap_val if category_label == "Total" else None,
            })

    logger.info("Parsed %d records for %s", len(records), trade_date)
    return records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _float_val(row: pd.Series, col: str) -> Optional[float]:
    val = row.get(col)
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f   # NaN → None
    except (ValueError, TypeError):
        return None


def _int_val(row: pd.Series, col: str) -> Optional[int]:
    f = _float_val(row, col)
    return int(round(f)) if f is not None else None


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_day(trade_date: date) -> Optional[list[dict]]:
    """Download and parse one trading day.  Returns None if no file available."""
    content = download_xlsx(trade_date)
    if content is None:
        return None
    return parse_xlsx(content, trade_date)
