"""
TRACE Treasury Monitor — Home page
"""
import pandas as pd
import plotly.express as px
import streamlit as st

from src import analytics, db
from src.ui_helpers import (
    PLOTLY_LAYOUT,
    SUBTYPE_COLOURS,
    load_data,
    metric_delta,
)

st.set_page_config(
    page_title="TRACE Treasury Monitor",
    page_icon="📊",
    layout="wide",
)

# ── Header ────────────────────────────────────────────────────────────────────

st.title("📊 TRACE Treasury Monitor")
st.caption(
    "FINRA TRACE daily aggregate trading data — US Treasury securities. "
    "Data published ~8 PM ET each business day."
)

# ── Latest date banner ────────────────────────────────────────────────────────

latest_str = db.get_latest_date()
if not latest_str:
    st.warning(
        "No data in the database yet.  "
        "Run `python scripts/backfill.py` to load historical data."
    )
    st.stop()

st.info(f"**Last updated:** {latest_str}", icon="🗓️")

# ── Load last 120 days (enough for 90d rolling) ───────────────────────────────

df = load_data(end_date=latest_str)

if df.empty:
    st.warning("Database contains no records.")
    st.stop()

# ── Headline metrics ──────────────────────────────────────────────────────────

summary = analytics.latest_day_summary(df)

if summary:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Total Volume (latest day)",
        f"${summary['total_volume']:.1f}bn",
    )
    col2.metric(
        "vs 20-day avg",
        metric_delta(summary["pct_vs_20d"]),
        delta=metric_delta(summary["pct_vs_20d"]),
        delta_color="normal",
    )
    col3.metric(
        "vs 90-day avg",
        metric_delta(summary["pct_vs_90d"]),
        delta=metric_delta(summary["pct_vs_90d"]),
        delta_color="normal",
    )
    col4.metric(
        "Total Trades",
        f"{summary['total_trades']:,}" if summary["total_trades"] else "—",
    )

st.divider()

# ── Alert panel ───────────────────────────────────────────────────────────────

st.subheader("🚨 Recent Anomalies (last 30 days)")
st.caption("Days where volume is >2σ from the 20-day or 90-day rolling average.")

alerts = analytics.get_recent_alerts(df, days=30, threshold=2.0)

if alerts:
    for alert in alerts[:15]:
        st.markdown(f"- {alert}")
    if len(alerts) > 15:
        st.caption(f"…and {len(alerts) - 15} more. See the Anomaly Detection page.")
else:
    st.success("No anomalies in the last 30 days.")

st.divider()

# ── 90-day volume sparklines by security subtype ──────────────────────────────

st.subheader("Volume by Security Type — last 90 days")

latest_date = df["trade_date"].max()
cutoff = latest_date - pd.Timedelta(days=90)
recent = df[df["trade_date"] >= cutoff]

# Use "Total" rows to avoid double-counting ATS + D2C
totals = recent[recent["trading_category"].str.lower().str.contains("total", na=False)]

if totals.empty:
    totals = recent

daily_by_subtype = (
    totals.groupby(["trade_date", "security_subtype"])["volume_par"]
    .sum()
    .reset_index()
)

if not daily_by_subtype.empty:
    fig = px.line(
        daily_by_subtype,
        x="trade_date",
        y="volume_par",
        color="security_subtype",
        color_discrete_map=SUBTYPE_COLOURS,
        labels={"trade_date": "Date", "volume_par": "Volume (par, $bn)", "security_subtype": "Type"},
        title="Daily Volume by Security Type",
    )
    fig.update_layout(**PLOTLY_LAYOUT)
    st.plotly_chart(fig, use_container_width=True)

# ── ATS vs D2C split (latest day) ────────────────────────────────────────────

st.subheader(f"ATS vs Dealer-to-Customer Split — {latest_str}")

latest_day = df[df["trade_date"] == latest_date]
cat_vol = (
    latest_day[~latest_day["trading_category"].str.lower().str.contains("total", na=False)]
    .groupby("trading_category")["volume_par"]
    .sum()
    .reset_index()
)

if not cat_vol.empty:
    col_left, col_right = st.columns([1, 2])
    with col_left:
        fig_pie = px.pie(
            cat_vol,
            names="trading_category",
            values="volume_par",
            color="trading_category",
            color_discrete_map={
                "ATS and Interdealer": "#1f4e79",
                "Dealer-to-Customer": "#2e86ab",
            },
            hole=0.4,
        )
        fig_pie.update_layout(
            template="plotly_white",
            margin=dict(l=10, r=10, t=30, b=10),
            showlegend=True,
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    with col_right:
        st.dataframe(
            cat_vol.rename(columns={"trading_category": "Category", "volume_par": "Volume ($bn)"}),
            hide_index=True,
            use_container_width=True,
        )

st.caption("Navigate using the sidebar to explore Volume Trends, Market Structure, Anomaly Detection, or Export.")
