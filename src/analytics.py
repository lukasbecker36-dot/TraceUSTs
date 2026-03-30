"""
Rolling statistics and anomaly detection for treasury trading data.

Data hierarchy (from the XLSX):
  Each (date, subtype, trading_category) combination has THREE row types:
    1. Aggregate row     — maturity_bucket=None, on_the_run=None
    2. Maturity row      — maturity_bucket set,  on_the_run=None  (OTR+OFR combined)
    3. OTR/OFR row       — maturity_bucket set,  on_the_run='On'|'Off'

  To avoid double-counting, always filter to ONE row type before summing.
  Use the helpers agg_only(), maturity_only(), otr_only() below.
"""
from typing import Optional

import numpy as np
import pandas as pd

_GROUP_COLS = ["security_subtype", "trading_category", "maturity_bucket", "on_the_run"]
_DEFAULT_WINDOWS = [20, 90]
_DEFAULT_THRESHOLD = 2.0


# ── Row-level filters (avoid double-counting) ─────────────────────────────────

def agg_only(df: pd.DataFrame) -> pd.DataFrame:
    """Subtype-level aggregates only (no maturity or OTR breakdown)."""
    return df[df["maturity_bucket"].isna() & df["on_the_run"].isna()]


def maturity_only(df: pd.DataFrame) -> pd.DataFrame:
    """Maturity-bucket aggregates (OTR+OFR combined, no OTR breakdown)."""
    return df[df["maturity_bucket"].notna() & df["on_the_run"].isna()]


def otr_only(df: pd.DataFrame) -> pd.DataFrame:
    """On-the-run / Off-the-run rows only."""
    return df[df["on_the_run"].notna()]


# ── Rolling stats ─────────────────────────────────────────────────────────────

def compute_rolling_stats(
    df: pd.DataFrame,
    value_col: str = "volume_par",
    windows: list[int] = None,
) -> pd.DataFrame:
    """
    Add rolling_mean_{W}d, rolling_std_{W}d, zscore_{W}d columns per window W.

    Works for both multi-group DataFrames (with _GROUP_COLS) and single-series
    DataFrames (e.g. after a daily groupby/sum).  Rolling stats are computed on
    the *shifted* series so today is compared against historical context only.
    """
    if windows is None:
        windows = _DEFAULT_WINDOWS

    df = df.sort_values("trade_date").copy()
    existing_groups = [c for c in _GROUP_COLS if c in df.columns]

    for w in windows:
        min_p = max(5, w // 4)

        if existing_groups:
            df[f"rolling_mean_{w}d"] = (
                df.groupby(existing_groups, dropna=False)[value_col]
                .transform(lambda x: x.shift(1).rolling(w, min_periods=min_p).mean())
            )
            df[f"rolling_std_{w}d"] = (
                df.groupby(existing_groups, dropna=False)[value_col]
                .transform(lambda x: x.shift(1).rolling(w, min_periods=min_p).std())
            )
        else:
            # Single series (no group columns present)
            shifted = df[value_col].shift(1)
            df[f"rolling_mean_{w}d"] = shifted.rolling(w, min_periods=min_p).mean()
            df[f"rolling_std_{w}d"] = shifted.rolling(w, min_periods=min_p).std()

        df[f"zscore_{w}d"] = (
            (df[value_col] - df[f"rolling_mean_{w}d"])
            / df[f"rolling_std_{w}d"].replace(0, np.nan)
        )

    return df


# ── Anomaly detection ─────────────────────────────────────────────────────────

def detect_anomalies(
    df: pd.DataFrame,
    value_col: str = "volume_par",
    threshold: float = _DEFAULT_THRESHOLD,
    windows: list[int] = None,
) -> pd.DataFrame:
    """
    Add is_anomaly, anomaly_window, anomaly_zscore columns.
    Calls compute_rolling_stats internally.
    """
    if windows is None:
        windows = _DEFAULT_WINDOWS

    df = compute_rolling_stats(df, value_col=value_col, windows=windows)
    df["is_anomaly"] = False
    df["anomaly_window"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df["anomaly_zscore"] = pd.array([pd.NA] * len(df), dtype="Float64")

    for w in windows:
        col = f"zscore_{w}d"
        if col not in df.columns:
            continue
        breached = df[col].abs() > threshold
        # Only set anomaly_window/zscore for the first window that fires
        new_breach = breached & df["anomaly_window"].isna()
        df.loc[new_breach, "anomaly_window"] = w
        df.loc[new_breach, "anomaly_zscore"] = df.loc[new_breach, col]
        df.loc[breached, "is_anomaly"] = True

    return df


# ── Alert formatting ──────────────────────────────────────────────────────────

def format_alert(row: pd.Series) -> str:
    direction = "above" if row["anomaly_zscore"] > 0 else "below"
    parts = [str(row["security_subtype"])]
    if pd.notna(row.get("maturity_bucket")):
        parts.append(str(row["maturity_bucket"]))
    if pd.notna(row.get("on_the_run")):
        parts.append(f"{'on' if row['on_the_run'] == 'On' else 'off'}-the-run")

    volume = row.get("volume_par")
    volume_str = f"${volume:.1f}bn" if pd.notna(volume) else "N/A"
    date_str = (
        row["trade_date"].strftime("%d %b %Y")
        if hasattr(row["trade_date"], "strftime")
        else str(row["trade_date"])
    )

    return (
        f"**{date_str}** — {' '.join(parts)} / {row['trading_category']}: "
        f"volume {volume_str} is **{abs(row['anomaly_zscore']):.1f}σ {direction}** "
        f"the {row['anomaly_window']}-day average"
    )


def get_recent_alerts(
    df: pd.DataFrame,
    days: int = 30,
    threshold: float = _DEFAULT_THRESHOLD,
    value_col: str = "volume_par",
) -> list[str]:
    """
    Return formatted alert strings for recent anomalies.
    Runs anomaly detection on aggregate-level rows only to avoid double-counting.
    """
    if df.empty:
        return []

    # Use subtype-level aggregates (no maturity or OTR breakdown)
    agg = agg_only(df)
    if agg.empty:
        agg = df

    with_stats = detect_anomalies(agg, value_col=value_col, threshold=threshold)
    cutoff = with_stats["trade_date"].max() - pd.Timedelta(days=days)
    recent = with_stats[
        with_stats["is_anomaly"] & (with_stats["trade_date"] >= cutoff)
    ].sort_values("trade_date", ascending=False)

    return [format_alert(row) for _, row in recent.iterrows()]


# ── Summary metrics ───────────────────────────────────────────────────────────

def latest_day_summary(df: pd.DataFrame) -> Optional[dict]:
    """
    Return headline metrics for the most recent trading day.

    Uses aggregate-level rows (maturity_bucket=None, on_the_run=None) with
    trading_category='Total' to avoid double-counting maturity/OTR sub-rows.
    """
    if df.empty:
        return None

    latest_date = df["trade_date"].max()

    # Top-level Total rows for the latest day
    top_totals = agg_only(df[df["trade_date"] == latest_date])
    top_totals = top_totals[
        top_totals["trading_category"].str.lower().str.contains("total", na=False)
    ]

    if top_totals.empty:
        return None

    total_volume = top_totals["volume_par"].sum()
    total_trades = top_totals["trade_count"].sum()

    # Rolling comparison — daily total volume across all subtypes
    all_top = agg_only(df)
    all_top = all_top[
        all_top["trading_category"].str.lower().str.contains("total", na=False)
    ]
    daily_vol = (
        all_top.groupby("trade_date")["volume_par"].sum().sort_index()
    )

    return {
        "date": latest_date,
        "total_volume": total_volume,
        "total_trades": int(total_trades) if pd.notna(total_trades) else None,
        "pct_vs_20d": _pct_vs_rolling(daily_vol, 20),
        "pct_vs_90d": _pct_vs_rolling(daily_vol, 90),
    }


def _pct_vs_rolling(series: pd.Series, window: int) -> Optional[float]:
    if len(series) < 2:
        return None
    current = series.iloc[-1]
    hist = series.iloc[-(window + 1) : -1]
    hist_mean = hist.mean()
    if hist_mean == 0 or pd.isna(hist_mean):
        return None
    return (current - hist_mean) / hist_mean * 100
