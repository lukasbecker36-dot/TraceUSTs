"""
Auction Allotments — who buys Treasuries at auction, by investor class.
Data: US Treasury investor class auction allotments (monthly).
"""
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
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

latest_str = db.get_auction_latest_issue_date()
latest = date.fromisoformat(latest_str) if latest_str else date.today()
default_start = latest - timedelta(days=5 * 365)

start = st.sidebar.date_input("From", value=default_start, key="aa_start")
end = st.sidebar.date_input("To", value=latest, key="aa_end")

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
        "Run the **Backfill Auction Allotments** GitHub Actions workflow to load historical data."
    )
    st.stop()

# Drop aggregate/total rows — any investor_class containing "total" is a sum column, not a class
df = df[~df["investor_class"].str.strip().str.lower().str.contains("total", na=False)]

# ── Tenor (security_type) selectors ──────────────────────────────────────────

st.sidebar.markdown("---")
st.sidebar.subheader("Tenor filter")

# Build per-series tenor lists from loaded data
bills_tenors = sorted(df[df["series"] == "Bills"]["security_type"].dropna().unique().tolist())
coupon_tenors = sorted(df[df["series"] == "Coupons"]["security_type"].dropna().unique().tolist())

selected_bills_tenors = bills_tenors
selected_coupon_tenors = coupon_tenors

if "Bills" in selected_series and bills_tenors:
    selected_bills_tenors = st.sidebar.multiselect(
        "Bills tenors", bills_tenors, default=bills_tenors, key="aa_bills_tenors"
    )

if "Coupons" in selected_series and coupon_tenors:
    selected_coupon_tenors = st.sidebar.multiselect(
        "Coupon tenors", coupon_tenors, default=coupon_tenors, key="aa_coupon_tenors"
    )

# Apply tenor filter
tenor_mask = pd.Series(False, index=df.index)
if "Bills" in selected_series and selected_bills_tenors:
    tenor_mask |= (df["series"] == "Bills") & df["security_type"].isin(selected_bills_tenors)
if "Coupons" in selected_series and selected_coupon_tenors:
    tenor_mask |= (df["series"] == "Coupons") & df["security_type"].isin(selected_coupon_tenors)

df = df[tenor_mask]

if df.empty:
    st.warning("No data for the selected tenors.")
    st.stop()

# ── Monthly column ─────────────────────────────────────────────────────────────

df["month"] = df["issue_date"].dt.to_period("M").dt.to_timestamp()

# ── 1. Summary metrics ────────────────────────────────────────────────────────

latest_date = df["issue_date"].max()
latest_df = df[df["issue_date"] == latest_date]

by_class_latest = latest_df.groupby("investor_class")["allotment_amt"].sum()

if not by_class_latest.empty:
    top_class = by_class_latest.idxmax()
    top_class_pct = by_class_latest.max() / by_class_latest.sum() * 100 if by_class_latest.sum() > 0 else 0
else:
    top_class, top_class_pct = "N/A", 0.0

col1, col2 = st.columns(2)
with col1:
    st.metric("Largest Investor Class", top_class, delta=f"{top_class_pct:.1f}% of allotments")
with col2:
    st.metric(f"Auctions — {latest_date.strftime('%d %b %Y')}", latest_df["cusip"].nunique())

# ── 2. Stacked area — investor class share over time ─────────────────────────

st.subheader("Investor Class Share of Total Allotments over Time")

mt = (
    df.groupby(["month", "series", "investor_class"])["allotment_amt"]
    .sum()
    .reset_index()
)
mt_total = mt.groupby(["month", "series"])["allotment_amt"].transform("sum")
mt["share_pct"] = mt["allotment_amt"] / mt_total * 100

if len(selected_series) > 1:
    tabs = st.tabs(selected_series)
else:
    tabs = [st.container()]

for tab, s in zip(tabs, selected_series):
    with tab:
        subset = mt[mt["series"] == s].sort_values(["month", "investor_class"])
        if subset.empty:
            st.info(f"No {s} data in this date range.")
            continue
        fig = px.line(
            subset,
            x="month",
            y="share_pct",
            color="investor_class",
            labels={"month": "Month", "share_pct": "Share (%)", "investor_class": "Investor Class"},
            title=f"{s} — Investor Class Share (%)",
        )
        fig.update_layout(**PLOTLY_LAYOUT)
        fig.update_traces(line=dict(width=1.5))
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
            labels={"allotment_amt": "Allotment ($m)", "investor_class": "Investor Class", "series": "Series"},
            title="Allotment by Investor Class ($m)",
        )
    else:
        fig_bar = px.bar(
            bar_data[bar_data["series"] == selected_series[0]],
            x="allotment_amt",
            y="investor_class",
            orientation="h",
            labels={"allotment_amt": "Allotment ($m)", "investor_class": "Investor Class"},
            title=f"{selected_series[0]} — Allotment by Investor Class ($m)",
            color_discrete_sequence=["#1f4e79"],
        )
    fig_bar.update_layout(**PLOTLY_LAYOUT, hovermode="y unified")
    st.plotly_chart(fig_bar, use_container_width=True)

# ── 4. Absolute allotment trends — top investor classes ───────────────────────

st.subheader("Allotment Trends by Investor Class ($m)")

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
                labels={"month": "Month", "allotment_amt": "Allotment ($m)", "investor_class": "Investor Class"},
                title=f"{s} — Monthly Allotments by Investor Class ($m)",
            )
            fig_line.update_layout(**PLOTLY_LAYOUT)
            fig_line.update_traces(line=dict(width=1.5))
            st.plotly_chart(fig_line, use_container_width=True)

# ── 5. CSV export ─────────────────────────────────────────────────────────────

st.subheader("Export Data")

export_cols = ["issue_date", "series", "security_type", "cusip", "maturity_date",
               "rate", "investor_class", "allotment_amt"]
export_col_names = ["Issue Date", "Series", "Security Type", "CUSIP", "Maturity Date",
                    "Rate", "Investor Class", "Allotment ($m)"]

ecol1, ecol2 = st.columns(2)

for col, s in zip([ecol1, ecol2], ["Bills", "Coupons"]):
    with col:
        s_df = df[df["series"] == s][export_cols].copy()
        s_df.columns = export_col_names
        s_df = s_df.sort_values(["Issue Date", "Security Type", "Investor Class"])
        st.download_button(
            label=f"Download {s} CSV",
            data=s_df.to_csv(index=False).encode("utf-8"),
            file_name=f"treasury_auction_allotments_{s.lower()}_{start}_{end}.csv",
            mime="text/csv",
            disabled=s_df.empty,
            key=f"dl_{s}",
        )
