"""
Market Structure — how volume is split across trading venues and security types.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import analytics
from src.ui_helpers import (
    CATEGORY_COLOURS,
    PLOTLY_LAYOUT,
    SUBTYPE_COLOURS,
    date_range_selector,
    load_data,
)

st.set_page_config(page_title="Market Structure | TRACE Treasury", layout="wide")
st.title("🏛️ Market Structure")
st.caption("How trading volume is distributed across venues, security types, and tenor.")

# ── Sidebar ───────────────────────────────────────────────────────────────────

start, end = date_range_selector(default_days=365)

# ── Data ──────────────────────────────────────────────────────────────────────

df = load_data(start_date=str(start), end_date=str(end))

if df.empty:
    st.warning("No data for the selected date range.")
    st.stop()

# Subtype-level aggregates only (maturity_bucket=None, on_the_run=None)
# This is the single correct row type for cross-subtype comparison.
agg = analytics.agg_only(df)

# ── 1. ATS vs D2C share over time ─────────────────────────────────────────────

st.subheader("ATS & Interdealer vs Dealer-to-Customer — volume share over time")

non_total_agg = agg[~agg["trading_category"].str.lower().str.contains("total", na=False)]
cat_daily = (
    non_total_agg.groupby(["trade_date", "trading_category"])["volume_par"]
    .sum()
    .reset_index()
)
cat_total = cat_daily.groupby("trade_date")["volume_par"].transform("sum")
cat_daily["share_pct"] = cat_daily["volume_par"] / cat_total * 100

fig_share = px.area(
    cat_daily.sort_values(["trade_date", "trading_category"]),
    x="trade_date",
    y="share_pct",
    color="trading_category",
    color_discrete_map=CATEGORY_COLOURS,
    labels={"trade_date": "Date", "share_pct": "Share (%)", "trading_category": "Category"},
    title="Trading Category Share of Total Volume (%)",
)
fig_share.update_layout(**PLOTLY_LAYOUT)
fig_share.update_yaxes(range=[0, 100])
st.plotly_chart(fig_share, use_container_width=True)

# ── 2. Volume by security subtype — stacked area ───────────────────────────────

st.subheader("Volume by Security Type over time")

total_agg = agg[agg["trading_category"].str.lower().str.contains("total", na=False)]
subtype_daily = (
    total_agg.groupby(["trade_date", "security_subtype"])["volume_par"]
    .sum()
    .reset_index()
)

fig_subtype = px.area(
    subtype_daily.sort_values(["trade_date", "security_subtype"]),
    x="trade_date",
    y="volume_par",
    color="security_subtype",
    color_discrete_map=SUBTYPE_COLOURS,
    labels={"trade_date": "Date", "volume_par": "Volume (par, $bn)", "security_subtype": "Type"},
    title="Total Volume by Security Type ($bn)",
)
fig_subtype.update_layout(**PLOTLY_LAYOUT)
st.plotly_chart(fig_subtype, use_container_width=True)

# ── 3. On-the-run vs Off-the-run (Nominal Coupons and TIPS only) ───────────────

st.subheader("On-the-Run vs Off-the-Run — Nominal Coupons & TIPS")

# Use OTR rows (on_the_run set), summed across all maturity buckets
otr_df = analytics.otr_only(df)
otr_df = otr_df[
    otr_df["security_subtype"].isin(["Nominal Coupons", "TIPS"])
    & otr_df["trading_category"].str.lower().str.contains("total", na=False)
]

if otr_df.empty:
    st.info("No on/off-the-run breakdown in the selected date range.")
else:
    otr_daily = (
        otr_df.groupby(["trade_date", "security_subtype", "on_the_run"])["volume_par"]
        .sum()
        .reset_index()
    )
    fig_otr = px.line(
        otr_daily.sort_values("trade_date"),
        x="trade_date",
        y="volume_par",
        color="on_the_run",
        facet_col="security_subtype",
        color_discrete_map={"On": "#1f4e79", "Off": "#a8c5e0"},
        labels={
            "trade_date": "Date",
            "volume_par": "Volume (par, $bn)",
            "on_the_run": "On/Off the Run",
        },
        title="On-the-Run vs Off-the-Run Volume",
    )
    fig_otr.update_layout(**PLOTLY_LAYOUT)
    st.plotly_chart(fig_otr, use_container_width=True)

# ── 4. Maturity breakdown heatmap (Nominal Coupons) ────────────────────────────

st.subheader("Volume by Maturity Bucket — Nominal Coupons (Total category)")

# Use maturity-level rows (maturity set, OTR not set = maturity aggregate)
mat_df = analytics.maturity_only(df)
mat_df = mat_df[
    (mat_df["security_subtype"] == "Nominal Coupons")
    & mat_df["trading_category"].str.lower().str.contains("total", na=False)
]

if mat_df.empty:
    st.info("No maturity-bucket breakdown available.")
else:
    mat_pivot = (
        mat_df.groupby(["trade_date", "maturity_bucket"])["volume_par"]
        .sum()
        .unstack("maturity_bucket")
        .fillna(0)
    )

    # Order maturity columns from short to long end
    MATURITY_ORDER = [
        "<= 2 years",
        "> 2 years and <= 3 years",
        "> 3 years and <= 5 years",
        "> 5 years and <= 7 years",
        "> 7 years and <= 10 years",
        "> 10 years and <= 20 years",
        "> 20 years",
    ]
    ordered = [c for c in MATURITY_ORDER if c in mat_pivot.columns]
    remaining = [c for c in mat_pivot.columns if c not in ordered]
    mat_pivot = mat_pivot[ordered + remaining]

    fig_heat = go.Figure(
        go.Heatmap(
            x=mat_pivot.index,
            y=mat_pivot.columns.tolist(),
            z=mat_pivot.values.T,
            colorscale="Blues",
            hoverongaps=False,
            colorbar=dict(title="$bn"),
        )
    )
    fig_heat.update_layout(
        title="Nominal Coupons Volume by Maturity Bucket ($bn)",
        xaxis_title="Date",
        yaxis_title="Remaining Maturity",
        template="plotly_white",
        font=dict(family="Arial, sans-serif", size=13),
        margin=dict(l=200, r=30, t=50, b=50),
        hovermode="closest",
    )
    st.plotly_chart(fig_heat, use_container_width=True)
