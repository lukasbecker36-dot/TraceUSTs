"""
Auction Allotments — who buys Treasuries at auction, by investor class.
Data: US Treasury investor class auction allotments (monthly).
"""
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import db
from src.ui_helpers import PLOTLY_LAYOUT

st.set_page_config(page_title="Auction Allotments | TRACE Treasury", layout="wide")
db.init_auction_db()
st.title("🏦 Who Buys Treasuries?")
st.caption(
    "Investor class allotments at US Treasury auctions. "
    "Data released monthly on the ~7th business day by the US Department of the Treasury."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.header("Filters")

# Date range
latest_str = db.get_auction_latest_issue_date()
if latest_str:
    latest = date.fromisoformat(latest_str)
else:
    latest = date.today()
default_start = latest - timedelta(days=5 * 365)

start = st.sidebar.date_input("From", value=default_start, key="aa_start")
end = st.sidebar.date_input("To", value=latest, key="aa_end")

# Series selector
series_opts = ["Bills", "Coupons"]
selected_series = st.sidebar.multiselect("Series", series_opts, default=series_opts, key="aa_series")

# ── Load data ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Loading auction data…")
def load_auction_data(start_date: str, end_date: str, series: tuple) -> pd.DataFrame:
    return db.get_auction_data(
        start_date=start_date,
        end_date=end_date,
        series=list(series) if series else None,
    )


if not selected_series:
    st.warning("Select at least one series (Bills or Coupons).")
    st.stop()

df = load_auction_data(str(start), str(end), tuple(sorted(selected_series)))

if df.empty:
    st.warning(
        "No auction allotments data in the selected range. "
        "Run `python scripts/backfill_auctions.py` to load historical data."
    )
    st.stop()

# ── Aggregate: monthly totals by investor class ───────────────────────────────

df["month"] = df["issue_date"].dt.to_period("M").dt.to_timestamp()

# ── 1. Summary metrics ────────────────────────────────────────────────────────

latest_date = df["issue_date"].max()
latest_df = df[df["issue_date"] == latest_date]

by_class_latest = latest_df.groupby("investor_class")["allotment_amt"].sum()

if not by_class_latest.empty:
    top_class = by_class_latest.idxmax()
    top_class_pct = by_class_latest.max() / by_class_latest.sum() * 100 if by_class_latest.sum() > 0 else 0
else:
    top_class = "N/A"
    top_class_pct = 0.0

col1, col2 = st.columns(2)
with col1:
    st.metric("Largest Investor Class", top_class, delta=f"{top_class_pct:.1f}% of allotments")
with col2:
    n_auctions = latest_df["cusip"].nunique()
    st.metric(f"Auctions — {latest_date.strftime('%d %b %Y')}", n_auctions)

# ── 2. Stacked area — investor class share over time ─────────────────────────

st.subheader("Investor Class Share of Total Allotments over Time")

qt = (
    df.groupby(["month", "series", "investor_class"])["allotment_amt"]
    .sum()
    .reset_index()
)
qt_total = qt.groupby(["month", "series"])["allotment_amt"].transform("sum")
qt["share_pct"] = qt["allotment_amt"] / qt_total * 100

# One tab per series
if len(selected_series) > 1:
    tabs = st.tabs(selected_series)
else:
    tabs = [st.container()]

for tab, s in zip(tabs, selected_series):
    with tab:
        subset = qt[qt["series"] == s].sort_values(["month", "investor_class"])
        if subset.empty:
            st.info(f"No {s} data in this date range.")
            continue
        fig = px.area(
            subset,
            x="month",
            y="share_pct",
            color="investor_class",
            labels={
                "month": "Month",
                "share_pct": "Share (%)",
                "investor_class": "Investor Class",
            },
            title=f"{s} — Investor Class Share (%)",
        )
        fig.update_layout(**PLOTLY_LAYOUT)
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(fig, use_container_width=True)

# ── 3. Latest auction — bar chart ─────────────────────────────────────────────

st.subheader(f"Latest Auction Breakdown — {latest_date.strftime('%d %b %Y')}")

bar_data = (
    latest_df.groupby(["series", "investor_class"])["allotment_amt"]
    .sum()
    .reset_index()
    .sort_values("allotment_amt", ascending=True)
)

if not bar_data.empty:
    if len(selected_series) > 1:
        fig_bar = px.bar(
            bar_data,
            x="allotment_amt",
            y="investor_class",
            color="series",
            barmode="group",
            orientation="h",
            labels={
                "allotment_amt": "Allotment ($m)",
                "investor_class": "Investor Class",
                "series": "Series",
            },
            title="Allotment by Investor Class ($m)",
        )
    else:
        fig_bar = px.bar(
            bar_data[bar_data["series"] == selected_series[0]],
            x="allotment_amt",
            y="investor_class",
            orientation="h",
            labels={
                "allotment_amt": "Allotment ($m)",
                "investor_class": "Investor Class",
            },
            title=f"{selected_series[0]} — Allotment by Investor Class ($m)",
            color_discrete_sequence=["#1f4e79"],
        )
    fig_bar.update_layout(**PLOTLY_LAYOUT, hovermode="y unified")
    st.plotly_chart(fig_bar, use_container_width=True)

# ── 4. Absolute allotment trends — top investor classes ───────────────────────

st.subheader("Allotment Trends by Investor Class ($m)")

# Top classes by total allotment over period
top_classes = (
    df.groupby("investor_class")["allotment_amt"]
    .sum()
    .nlargest(8)
    .index.tolist()
)

line_data = (
    df[df["investor_class"].isin(top_classes)]
    .groupby(["month", "series", "investor_class"])["allotment_amt"]
    .sum()
    .reset_index()
)

if not line_data.empty:
    if len(selected_series) > 1:
        tabs2 = st.tabs(selected_series)
    else:
        tabs2 = [st.container()]

    for tab, s in zip(tabs2, selected_series):
        with tab:
            subset = line_data[line_data["series"] == s].sort_values(["investor_class", "month"])
            if subset.empty:
                st.info(f"No {s} data in this date range.")
                continue
            fig_line = px.line(
                subset,
                x="month",
                y="allotment_amt",
                color="investor_class",
                labels={
                    "month": "Month",
                    "allotment_amt": "Allotment ($m)",
                    "investor_class": "Investor Class",
                },
                title=f"{s} — Monthly Allotments by Investor Class ($m)",
            )
            fig_line.update_layout(**PLOTLY_LAYOUT)
            fig_line.update_traces(line=dict(width=1.5))
            st.plotly_chart(fig_line, use_container_width=True)

# ── 5. Raw data table ─────────────────────────────────────────────────────────

with st.expander("Raw data — latest auction date"):
    tbl = latest_df[["series", "security_type", "cusip", "maturity_date",
                      "rate", "investor_class", "allotment_amt"]].copy()
    tbl = tbl.sort_values(["series", "security_type", "cusip", "investor_class"])
    tbl.columns = ["Series", "Security Type", "CUSIP", "Maturity",
                   "Rate", "Investor Class", "Allotment ($m)"]
    st.dataframe(tbl, use_container_width=True, hide_index=True)
