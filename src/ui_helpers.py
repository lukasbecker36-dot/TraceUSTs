"""Shared UI utilities for the Streamlit dashboard."""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import db

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORY_COLOURS = {
    "ATS and Interdealer": "#1f4e79",
    "Dealer-to-Customer": "#2e86ab",
    "Total": "#a8dadc",
}

SUBTYPE_COLOURS = {
    "Bills": "#264653",
    "FRN": "#2a9d8f",
    "Nominal Coupons": "#e9c46a",
    "TIPS": "#f4a261",
}

OTR_COLOURS = {"On": "#1f4e79", "Off": "#a8c5e0"}

PLOTLY_LAYOUT = dict(
    template="plotly_white",
    font=dict(family="Arial, sans-serif", size=13),
    margin=dict(l=60, r=30, t=50, b=50),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)

# ── Cached data loader ────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Loading data…")
def load_data(
    start_date: str = None,
    end_date: str = None,
    security_subtypes: tuple = None,
    trading_categories: tuple = None,
) -> pd.DataFrame:
    return db.get_data(
        start_date=start_date,
        end_date=end_date,
        security_subtypes=list(security_subtypes) if security_subtypes else None,
        trading_categories=list(trading_categories) if trading_categories else None,
    )


@st.cache_data(ttl=3600)
def load_distinct(column: str) -> list:
    return db.get_distinct_values(column)


# ── Sidebar controls ──────────────────────────────────────────────────────────

def date_range_selector(default_days: int = 365) -> tuple[date, date]:
    st.sidebar.header("Date range")
    latest_str = db.get_latest_date()
    latest = date.fromisoformat(latest_str) if latest_str else date.today()
    default_start = latest - timedelta(days=default_days)

    start = st.sidebar.date_input("From", value=default_start, key="start_date")
    end = st.sidebar.date_input("To", value=latest, key="end_date")
    return start, end


def subtype_selector(label: str = "Security subtype", key: str = "subtypes") -> list[str]:
    options = load_distinct("security_subtype")
    return st.sidebar.multiselect(label, options, default=options, key=key)


def category_selector(
    label: str = "Trading category", key: str = "categories"
) -> list[str]:
    options = load_distinct("trading_category")
    # Default to all except 'Total' to avoid double-counting in charts
    default = [o for o in options if "total" not in o.lower()]
    return st.sidebar.multiselect(label, options, default=default, key=key)


# ── Chart helpers ─────────────────────────────────────────────────────────────

def metric_delta(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}{suffix}"


def add_anomaly_markers(
    fig: go.Figure,
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    name: str = "Anomaly",
) -> go.Figure:
    anomalies = df[df["is_anomaly"]]
    if anomalies.empty:
        return fig
    fig.add_trace(
        go.Scatter(
            x=anomalies[x_col],
            y=anomalies[y_col],
            mode="markers",
            marker=dict(color="red", size=9, symbol="circle-open", line=dict(width=2)),
            name=name,
            hovertemplate=(
                "%{x|%d %b %Y}<br>"
                f"{y_col}: %{{y:.2f}}<br>"
                "z-score: %{customdata:.1f}σ<extra></extra>"
            ),
            customdata=anomalies["anomaly_zscore"].abs(),
        )
    )
    return fig
