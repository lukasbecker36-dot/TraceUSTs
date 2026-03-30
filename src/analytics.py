"""
Rolling statistics and anomaly detection for treasury trading data.

All functions operate on DataFrames returned by db.get_data().
"""
from typing import Optional

import numpy as np
import pandas as pd

_GROUP_COLS = ["security_subtype", "trading_category", "maturity_bucket", "on_the_run"]
_DEFAULT_WINDOWS = [20, 90]
_DEFAULT_THRESHOLD = 2.0


# ── Rolling stats ─────────────────────────────────────────────────────────────

def compute_rolling_stats(
    df: pd.DataFrame,
    value_col: str = "volume_par",
    windows: list[int] = None,
) -> pd.DataFrame:
    """
    Add rolling mean, std, and z-score columns for each group × window.

    Rolling statistics are computed on the *shifted* series (excluding today)
    so we're comparing today against historical context only.

    Columns added per window W:
        rolling_mean_{W}d, rolling_std_{W}d, zscore_{W}d
    """
    if windows is None:
        windows = _DEFAULT_WINDOWS

    df = df.sort_values("trade_date").copy()
    groups = df.groupby(_GROUP_COLS, dropna=False)[value_col]

    for w in windows:
        min_p = max(5, w // 4)
        shifted = groups.transform(lambda x: x.shift(1))
        df[f"rolling_mean_{w}d"] = (
            shifted.groupby(
                [df[c] for c in _GROUP_COLS], dropna=False
            ).transform(lambda x: x.rolling(w, min_periods=min_p).mean())
        )
        df[f"rolling_std_{w}d"] = (
            shifted.groupby(
                [df[c] for c in _GROUP_COLS], dropna=False
            ).transform(lambda x: x.rolling(w, min_periods=min_p).std())
        )
        mean_col = f"rolling_mean_{w}d"
        std_col = f"rolling_std_{w}d"
        df[f"zscore_{w}d"] = (df[value_col] - df[mean_col]) / df[std_col].replace(0, np.nan)

    return df


def compute_rolling_stats_for_group(
    df: pd.DataFrame,
    value_col: str = "volume_par",
    windows: list[int] = None,
) -> pd.DataFrame:
    """
    Faster version for a pre-filtered single group (one series).
    Call this when the caller has already filtered to one subtype/category.
    """
    if windows is None:
        windows = _DEFAULT_WINDOWS

    df = df.sort_values("trade_date").copy()
    series = df[value_col]

    for w in windows:
        min_p = max(5, w // 4)
        shifted = series.shift(1)
        df[f"rolling_mean_{w}d"] = shifted.rolling(w, min_periods=min_p).mean()
        df[f"rolling_std_{w}d"] = shifted.rolling(w, min_periods=min_p).std()
        df[f"zscore_{w}d"] = (
            (series - df[f"rolling_mean_{w}d"]) / df[f"rolling_std_{w}d"].replace(0, np.nan)
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

    An anomaly is triggered when |z-score| > threshold on *any* window.
    The first window to breach is stored in anomaly_window.
    """
    if windows is None:
        windows = _DEFAULT_WINDOWS

    df = compute_rolling_stats(df, value_col=value_col, windows=windows)
    df["is_anomaly"] = False
    df["anomaly_window"] = pd.NA
    df["anomaly_zscore"] = pd.NA

    for w in windows:
        col = f"zscore_{w}d"
        breached = df[col].abs() > threshold
        # Only set anomaly_window/zscore on the first window that fires
        new_breach = breached & df["anomaly_window"].isna()
        df.loc[new_breach, "anomaly_window"] = w
        df.loc[new_breach, "anomaly_zscore"] = df.loc[new_breach, col]
        df.loc[breached, "is_anomaly"] = True

    return df


# ── Alert formatting ──────────────────────────────────────────────────────────

def format_alert(row: pd.Series) -> str:
    direction = "above" if row["anomaly_zscore"] > 0 else "below"
    subtype = row["security_subtype"]
    category = row["trading_category"]

    parts = [subtype]
    if pd.notna(row.get("maturity_bucket")):
        parts.append(row["maturity_bucket"])
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
        f"**{date_str}** — {' '.join(parts)} / {category}: "
        f"volume {volume_str} is **{abs(row['anomaly_zscore']):.1f}σ {direction}** "
        f"the {row['anomaly_window']}-day average"
    )


def get_recent_alerts(
    df: pd.DataFrame,
    days: int = 30,
    threshold: float = _DEFAULT_THRESHOLD,
    value_col: str = "volume_par",
) -> list[str]:
    """Return formatted alert strings for the most recent `days` trading days."""
    if df.empty:
        return []

    with_stats = detect_anomalies(df, value_col=value_col, threshold=threshold)
    cutoff = with_stats["trade_date"].max() - pd.Timedelta(days=days)
    recent = with_stats[with_stats["is_anomaly"] & (with_stats["trade_date"] >= cutoff)]
    recent = recent.sort_values("trade_date", ascending=False)

    return [format_alert(row) for _, row in recent.iterrows()]


# ── Summary metrics ───────────────────────────────────────────────────────────

def latest_day_summary(df: pd.DataFrame) -> Optional[dict]:
    """Return headline metrics for the most recent trading day."""
    if df.empty:
        return None

    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date]

    # Total row = trading_category == "Total" (or fall back to all rows)
    totals = latest[latest["trading_category"].str.lower().str.contains("total", na=False)]
    if totals.empty:
        totals = latest

    total_volume = totals["volume_par"].sum()
    total_trades = totals["trade_count"].sum()

    # Compare to rolling averages using full dataset Total rows
    all_totals = df[df["trading_category"].str.lower().str.contains("total", na=False)]
    daily_vol = (
        all_totals.groupby("trade_date")["volume_par"].sum().sort_index()
    )

    pct_vs_20d = _pct_vs_rolling(daily_vol, 20)
    pct_vs_90d = _pct_vs_rolling(daily_vol, 90)

    return {
        "date": latest_date,
        "total_volume": total_volume,
        "total_trades": int(total_trades) if pd.notna(total_trades) else None,
        "pct_vs_20d": pct_vs_20d,
        "pct_vs_90d": pct_vs_90d,
    }


def _pct_vs_rolling(series: pd.Series, window: int) -> Optional[float]:
    if len(series) < 2:
        return None
    current = series.iloc[-1]
    hist_mean = series.iloc[-(window + 1) : -1].mean()
    if hist_mean == 0 or pd.isna(hist_mean):
        return None
    return (current - hist_mean) / hist_mean * 100
