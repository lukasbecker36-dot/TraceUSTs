"""
Anomaly Detection — statistical outliers vs rolling averages.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import analytics, db
from src.ui_helpers import (
    CATEGORY_COLOURS,
    PLOTLY_LAYOUT,
    SUBTYPE_COLOURS,
    date_range_selector,
    load_data,
    load_distinct,
)

st.set_page_config(page_title="Anomaly Detection | TRACE Treasury", layout="wide")
db.init_db()
st.title("🔍 Anomaly Detection")
st.caption(
    "Flags trading days where volume deviates significantly from its rolling average. "
    "Useful for spotting unusual market activity worth investigating."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────

start, end = date_range_selector(default_days=365)

with st.sidebar:
    st.header("Detection settings")
    threshold = st.slider(
        "Alert threshold (σ)",
        min_value=1.0, max_value=4.0, value=2.0, step=0.25,
        key="ad_threshold",
    )
    windows = st.multiselect(
        "Rolling windows (days)",
        options=[10, 20, 30, 60, 90],
        default=[20, 90],
        key="ad_windows",
    )

    st.header("Focus on")
    subtype_opts = load_distinct("security_subtype")
    selected_subtype = st.selectbox("Security subtype", ["All"] + subtype_opts, key="ad_subtype")
    category_opts = load_distinct("trading_category")
    selected_category = st.selectbox("Trading category", ["All"] + category_opts, key="ad_cat")

if not windows:
    st.warning("Select at least one rolling window.")
    st.stop()

# ── Data ──────────────────────────────────────────────────────────────────────

subtype_filter = None if selected_subtype == "All" else [selected_subtype]
category_filter = None if selected_category == "All" else [selected_category]

df = load_data(
    start_date=str(start),
    end_date=str(end),
    security_subtypes=tuple(subtype_filter) if subtype_filter else None,
    trading_categories=tuple(category_filter) if category_filter else None,
)

if df.empty:
    st.warning("No data for the selected filters.")
    st.stop()

# Use aggregate-level rows only (no maturity/OTR sub-rows) to avoid double-counting
# Then filter to the non-Total trading categories before summing
df_agg = analytics.agg_only(df)
if df_agg.empty:
    df_agg = df

non_total = df_agg[~df_agg["trading_category"].str.lower().str.contains("total", na=False)]
if non_total.empty:
    non_total = df_agg

daily = (
    non_total.groupby("trade_date")["volume_par"]
    .sum()
    .reset_index()
    .sort_values("trade_date")
)

# ── Compute rolling stats + anomalies ─────────────────────────────────────────

daily_stats = analytics.detect_anomalies(
    daily,
    value_col="volume_par",
    threshold=threshold,
    windows=windows,
)

anomalies = daily_stats[daily_stats["is_anomaly"]]

# ── Alert banner ──────────────────────────────────────────────────────────────

recent_cutoff = daily_stats["trade_date"].max() - pd.Timedelta(days=30)
recent_anomalies = anomalies[anomalies["trade_date"] >= recent_cutoff]

if not recent_anomalies.empty:
    with st.expander(f"🚨 {len(recent_anomalies)} anomalies in the last 30 days", expanded=True):
        for _, row in recent_anomalies.sort_values("trade_date", ascending=False).iterrows():
            direction = "above" if row["anomaly_zscore"] > 0 else "below"
            vol = row.get("volume_par")
            vol_str = f"${vol:.1f}bn" if pd.notna(vol) else "N/A"
            st.markdown(
                f"- **{row['trade_date'].strftime('%d %b %Y')}** — "
                f"volume {vol_str} is **{abs(row['anomaly_zscore']):.1f}σ {direction}** "
                f"the {row['anomaly_window']}-day average"
            )
else:
    st.success("No anomalies in the last 30 days for this selection.")

# ── Main chart ────────────────────────────────────────────────────────────────

st.subheader("Volume with rolling averages and anomalies")

fig = go.Figure()

# Raw volume line
fig.add_trace(go.Scatter(
    x=daily_stats["trade_date"],
    y=daily_stats["volume_par"],
    mode="lines",
    name="Daily Volume",
    line=dict(color="#1f4e79", width=1.5),
    hovertemplate="%{x|%d %b %Y}<br>Volume: $%{y:.2f}bn<extra></extra>",
))

# Rolling mean lines and bands
window_colours = {20: "#e76f51", 90: "#2a9d8f", 10: "#f4a261", 30: "#264653", 60: "#457b9d"}
for w in windows:
    mean_col = f"rolling_mean_{w}d"
    std_col = f"rolling_std_{w}d"
    colour = window_colours.get(w, "#888888")

    if mean_col not in daily_stats.columns:
        continue

    fig.add_trace(go.Scatter(
        x=daily_stats["trade_date"],
        y=daily_stats[mean_col],
        mode="lines",
        name=f"{w}-day avg",
        line=dict(color=colour, width=1, dash="dash"),
        hovertemplate=f"%{{x|%d %b %Y}}<br>{w}d avg: $%{{y:.2f}}bn<extra></extra>",
    ))

    # ±threshold band
    upper = daily_stats[mean_col] + threshold * daily_stats[std_col]
    lower = daily_stats[mean_col] - threshold * daily_stats[std_col]

    fig.add_trace(go.Scatter(
        x=pd.concat([daily_stats["trade_date"], daily_stats["trade_date"].iloc[::-1]]),
        y=pd.concat([upper, lower.iloc[::-1]]),
        fill="toself",
        fillcolor=f"rgba{tuple(int(colour.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + (0.08,)}",
        line=dict(color="rgba(255,255,255,0)"),
        showlegend=False,
        name=f"{w}d ±{threshold}σ band",
        hoverinfo="skip",
    ))

# Anomaly markers
if not anomalies.empty:
    fig.add_trace(go.Scatter(
        x=anomalies["trade_date"],
        y=anomalies["volume_par"],
        mode="markers",
        name=f"Anomaly (>{threshold}σ)",
        marker=dict(color="red", size=9, symbol="circle-open", line=dict(width=2)),
        hovertemplate=(
            "%{x|%d %b %Y}<br>"
            "Volume: $%{y:.2f}bn<br>"
            "z-score: %{customdata:.1f}σ<extra></extra>"
        ),
        customdata=anomalies["anomaly_zscore"].abs(),
    ))

fig.update_layout(
    **PLOTLY_LAYOUT,
    title="Daily Volume with Rolling Averages and Anomaly Flags",
    xaxis_title="Date",
    yaxis_title="Volume (par, $bn)",
)
st.plotly_chart(fig, use_container_width=True)

# ── Z-score chart ─────────────────────────────────────────────────────────────

st.subheader("Z-scores over time")

fig_z = go.Figure()

for w in windows:
    col = f"zscore_{w}d"
    if col not in daily_stats.columns:
        continue
    colour = window_colours.get(w, "#888888")
    fig_z.add_trace(go.Scatter(
        x=daily_stats["trade_date"],
        y=daily_stats[col],
        mode="lines",
        name=f"{w}-day z",
        line=dict(color=colour, width=1.5),
    ))

# Threshold lines
for sign, label in [(threshold, f"+{threshold}σ"), (-threshold, f"−{threshold}σ")]:
    fig_z.add_hline(
        y=sign,
        line_dash="dot",
        line_color="red",
        annotation_text=label,
        annotation_position="right",
    )
fig_z.add_hline(y=0, line_color="grey", line_width=0.5)

fig_z.update_layout(
    **PLOTLY_LAYOUT,
    title="Volume Z-Score vs Rolling Average",
    xaxis_title="Date",
    yaxis_title="Standard Deviations (σ)",
)
st.plotly_chart(fig_z, use_container_width=True)
st.caption(
    "A z-score measures how far today's volume is from its recent average, expressed in standard deviations (σ). "
    "A score of +2 means volume was unusually high — two standard deviations above the rolling mean — "
    "while −2 means unusually low. Scores beyond ±2σ are statistically uncommon and typically warrant a closer look."
)

# ── Anomaly table ─────────────────────────────────────────────────────────────

st.subheader("All anomalies in selected range")

if anomalies.empty:
    st.info("No anomalies detected with the current settings.")
else:
    display_cols = ["trade_date", "volume_par", "anomaly_window", "anomaly_zscore"]
    display_cols = [c for c in display_cols if c in anomalies.columns]
    tbl = anomalies[display_cols].sort_values("trade_date", ascending=False).copy()
    tbl["trade_date"] = tbl["trade_date"].dt.strftime("%Y-%m-%d")
    tbl["anomaly_zscore"] = pd.to_numeric(tbl["anomaly_zscore"], errors="coerce").round(2)
    tbl["volume_par"] = tbl["volume_par"].round(2)
    tbl.columns = ["Date", "Volume ($bn)", "Window (days)", "Z-score"]
    st.dataframe(tbl, use_container_width=True, hide_index=True)
