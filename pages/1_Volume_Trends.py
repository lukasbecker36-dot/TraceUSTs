"""
Volume Trends — interactive time-series explorer.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.ui_helpers import (
    CATEGORY_COLOURS,
    PLOTLY_LAYOUT,
    SUBTYPE_COLOURS,
    category_selector,
    date_range_selector,
    load_data,
    subtype_selector,
)

st.set_page_config(page_title="Volume Trends | TRACE Treasury", layout="wide")
st.title("📈 Volume Trends")
st.caption("Daily par-value trading volume by security type and trading category.")

# ── Sidebar ───────────────────────────────────────────────────────────────────

start, end = date_range_selector(default_days=365)
subtypes = subtype_selector(key="vt_subtypes")
categories = category_selector(key="vt_categories")

with st.sidebar:
    st.header("Filters")
    maturity_opts = ["All"]
    show_otr = st.checkbox("Show On/Off-the-run breakdown", value=False, key="vt_otr")
    smooth = st.selectbox(
        "Smoothing (rolling avg)",
        options=[1, 5, 20],
        index=0,
        format_func=lambda x: "None" if x == 1 else f"{x}-day",
        key="vt_smooth",
    )

# ── Data ──────────────────────────────────────────────────────────────────────

if not subtypes or not categories:
    st.warning("Select at least one security subtype and trading category.")
    st.stop()

df = load_data(
    start_date=str(start),
    end_date=str(end),
    security_subtypes=tuple(sorted(subtypes)),
    trading_categories=tuple(sorted(categories)),
)

if df.empty:
    st.warning("No data for the selected filters.")
    st.stop()

# ── Aggregate ─────────────────────────────────────────────────────────────────

if show_otr:
    group_cols = ["trade_date", "security_subtype", "trading_category", "on_the_run"]
    colour_col = "on_the_run"
    colour_map = {"On": "#1f4e79", "Off": "#a8c5e0"}
    facet_col = "security_subtype"
else:
    group_cols = ["trade_date", "security_subtype", "trading_category"]
    colour_col = "trading_category"
    colour_map = CATEGORY_COLOURS
    facet_col = None

agg = (
    df.groupby(group_cols, dropna=False)["volume_par"]
    .sum()
    .reset_index()
    .sort_values("trade_date")
)

if smooth > 1:
    smooth_groups = [c for c in group_cols if c != "trade_date"]
    agg["volume_par"] = (
        agg.groupby(smooth_groups, dropna=False)["volume_par"]
        .transform(lambda x: x.rolling(smooth, min_periods=1).mean())
    )

# ── Line chart ────────────────────────────────────────────────────────────────

facet_kwargs = dict(facet_col=facet_col, facet_col_wrap=2) if facet_col else {}

fig = px.line(
    agg,
    x="trade_date",
    y="volume_par",
    color=colour_col,
    color_discrete_map=colour_map,
    labels={
        "trade_date": "Date",
        "volume_par": "Volume (par, $bn)",
        colour_col: colour_col.replace("_", " ").title(),
    },
    title="Daily Trading Volume",
    **facet_kwargs,
)
fig.update_layout(**PLOTLY_LAYOUT)
fig.update_traces(line=dict(width=1.5))
st.plotly_chart(fig, use_container_width=True)

# ── Bar chart — recent 30 days ────────────────────────────────────────────────

st.subheader("Recent 30 days — bar view")

cutoff = agg["trade_date"].max() - pd.Timedelta(days=30)
recent = agg[agg["trade_date"] >= cutoff]

fig_bar = px.bar(
    recent,
    x="trade_date",
    y="volume_par",
    color=colour_col,
    color_discrete_map=colour_map,
    barmode="group",
    labels={
        "trade_date": "Date",
        "volume_par": "Volume (par, $bn)",
        colour_col: colour_col.replace("_", " ").title(),
    },
)
fig_bar.update_layout(**PLOTLY_LAYOUT)
st.plotly_chart(fig_bar, use_container_width=True)

# ── Summary table ─────────────────────────────────────────────────────────────

st.subheader("Summary statistics")

summary_cols = [c for c in group_cols if c != "trade_date"]
summary = (
    agg.groupby(summary_cols, dropna=False)["volume_par"]
    .agg(["mean", "median", "std", "min", "max"])
    .round(2)
    .reset_index()
)
summary.columns = [c.replace("_", " ").title() for c in summary.columns]
st.dataframe(summary, use_container_width=True, hide_index=True)
