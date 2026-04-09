"""
Market Structure — how volume is split across trading venues and security types.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import analytics, db
from src.ui_helpers import (
    CATEGORY_COLOURS,
    PLOTLY_LAYOUT,
    SUBTYPE_COLOURS,
    date_range_selector,
    load_data,
)

st.set_page_config(page_title="Market Structure | TRACE Treasury", layout="wide")
db.init_db()
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
        otr_daily.sort_values(["security_subtype", "on_the_run", "trade_date"]),
        x="trade_date",
        y="volume_par",
        color="on_the_run",
        facet_row="security_subtype",
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
    mat_line = (
        mat_df.groupby(["trade_date", "maturity_bucket"])["volume_par"]
        .sum()
        .reset_index()
    )

    MATURITY_ORDER = [
        "<= 2 years",
        "> 2 years and <= 3 years",
        "> 3 years and <= 5 years",
        "> 5 years and <= 7 years",
        "> 7 years and <= 10 years",
        "> 10 years and <= 20 years",
        "> 20 years",
    ]
    MATURITY_COLOURS = {
        "<= 2 years":               "#264653",
        "> 2 years and <= 3 years": "#2a9d8f",
        "> 3 years and <= 5 years": "#57cc99",
        "> 5 years and <= 7 years": "#e9c46a",
        "> 7 years and <= 10 years":"#f4a261",
        "> 10 years and <= 20 years":"#e76f51",
        "> 20 years":               "#9b2226",
    }

    # Enforce display order
    mat_line["maturity_bucket"] = pd.Categorical(
        mat_line["maturity_bucket"], categories=MATURITY_ORDER, ordered=True
    )
    mat_line = mat_line.sort_values(["maturity_bucket", "trade_date"])

    fig_mat_line = px.line(
        mat_line,
        x="trade_date",
        y="volume_par",
        color="maturity_bucket",
        color_discrete_map=MATURITY_COLOURS,
        category_orders={"maturity_bucket": MATURITY_ORDER},
        labels={
            "trade_date": "Date",
            "volume_par": "Volume (par, $bn)",
            "maturity_bucket": "Maturity",
        },
        title="Nominal Coupons Volume by Maturity Bucket over Time ($bn)",
    )
    fig_mat_line.update_layout(**PLOTLY_LAYOUT)
    fig_mat_line.update_traces(line=dict(width=1.5))
    st.plotly_chart(fig_mat_line, use_container_width=True)

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

# ── 5. Belly vs Long end ───────────────────────────────────────────────────────

st.subheader("Belly vs Long End — Nominal Coupons & TIPS")
st.caption("Belly: ≥2y to <10y  |  Long: ≥10y")

_BELLY_BUCKETS = {
    "<= 2 years",
    "> 2 years and <= 3 years",
    "> 3 years and <= 5 years",
    "> 5 years and <= 7 years",
    "> 7 years and <= 10 years",
}
_LONG_BUCKETS = {
    "> 10 years and <= 20 years",
    "> 20 years",
}

bl_df = analytics.maturity_only(df)
bl_df = bl_df[
    bl_df["security_subtype"].isin(["Nominal Coupons", "TIPS"])
    & bl_df["trading_category"].str.lower().str.contains("total", na=False)
].copy()

if bl_df.empty:
    st.info("No maturity-bucket data available for belly/long comparison.")
else:
    bl_df["tenor_group"] = bl_df["maturity_bucket"].map(
        lambda b: "Belly" if b in _BELLY_BUCKETS else ("Long" if b in _LONG_BUCKETS else None)
    )
    bl_df = bl_df[bl_df["tenor_group"].notna()]

    bl_daily = (
        bl_df.groupby(["trade_date", "security_subtype", "tenor_group"])["volume_par"]
        .sum()
        .reset_index()
    )

    bl_tabs = st.tabs(["Nominal Coupons", "TIPS"])
    for tab, subtype in zip(bl_tabs, ["Nominal Coupons", "TIPS"]):
        with tab:
            subset = bl_daily[bl_daily["security_subtype"] == subtype]
            if subset.empty:
                st.info(f"No {subtype} belly/long data in this date range.")
                continue

            fig_bl = px.line(
                subset.sort_values(["tenor_group", "trade_date"]),
                x="trade_date",
                y="volume_par",
                color="tenor_group",
                color_discrete_map={"Belly": "#2a9d8f", "Long": "#e76f51"},
                labels={
                    "trade_date": "Date",
                    "volume_par": "Volume (par, $bn)",
                    "tenor_group": "Tenor Group",
                },
                title=f"{subtype} — Belly vs Long End Volume ($bn)",
            )
            fig_bl.update_layout(**PLOTLY_LAYOUT)
            fig_bl.update_traces(line=dict(width=1.5))
            st.plotly_chart(fig_bl, use_container_width=True)

            # Share chart
            bl_total = bl_daily[bl_daily["security_subtype"] == subtype].copy()
            bl_total["total"] = bl_total.groupby("trade_date")["volume_par"].transform("sum")
            bl_total["share_pct"] = bl_total["volume_par"] / bl_total["total"] * 100

            fig_bl_share = px.line(
                bl_total.sort_values(["tenor_group", "trade_date"]),
                x="trade_date",
                y="share_pct",
                color="tenor_group",
                color_discrete_map={"Belly": "#2a9d8f", "Long": "#e76f51"},
                labels={
                    "trade_date": "Date",
                    "share_pct": "Share (%)",
                    "tenor_group": "Tenor Group",
                },
                title=f"{subtype} — Belly vs Long End Share of Coupon Volume (%)",
            )
            fig_bl_share.update_layout(**PLOTLY_LAYOUT)
            fig_bl_share.update_traces(line=dict(width=1.5))
            st.plotly_chart(fig_bl_share, use_container_width=True)
