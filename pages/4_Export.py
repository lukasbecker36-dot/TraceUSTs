"""
Export — download filtered data as CSV.
"""
import io

import pandas as pd
import streamlit as st

from src.ui_helpers import (
    category_selector,
    date_range_selector,
    load_data,
    load_distinct,
    subtype_selector,
)

st.set_page_config(page_title="Export | TRACE Treasury", layout="wide")
st.title("⬇️ Export Data")
st.caption("Filter the dataset and download as CSV.")

# ── Sidebar ───────────────────────────────────────────────────────────────────

start, end = date_range_selector(default_days=90)
subtypes = subtype_selector(label="Security subtype", key="ex_subtypes")
categories = category_selector(label="Trading category", key="ex_categories")

with st.sidebar:
    st.header("Additional filters")

    maturity_opts = ["All"] + load_distinct("maturity_bucket")
    selected_maturity = st.multiselect(
        "Maturity bucket",
        options=load_distinct("maturity_bucket"),
        default=load_distinct("maturity_bucket"),
        key="ex_maturity",
    )

    otr_opts = load_distinct("on_the_run")
    selected_otr = st.multiselect(
        "On/Off-the-run",
        options=otr_opts,
        default=otr_opts,
        key="ex_otr",
    )

# ── Data ──────────────────────────────────────────────────────────────────────

if not subtypes or not categories:
    st.warning("Select at least one security subtype and category.")
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

# Apply maturity and OTR filters (these aren't in the cached db call)
if selected_maturity:
    df = df[df["maturity_bucket"].isin(selected_maturity) | df["maturity_bucket"].isna()]
if selected_otr:
    df = df[df["on_the_run"].isin(selected_otr) | df["on_the_run"].isna()]

# ── Preview ───────────────────────────────────────────────────────────────────

st.subheader(f"Preview — {len(df):,} rows")

display = df.copy()
display["trade_date"] = display["trade_date"].dt.strftime("%Y-%m-%d")
display["volume_par"] = display["volume_par"].round(4)
display["vwap"] = display["vwap"].round(4)

st.dataframe(display.head(200), use_container_width=True, hide_index=True)

if len(df) > 200:
    st.caption(f"Showing first 200 rows of {len(df):,}. Full dataset included in the download.")

# ── Download ──────────────────────────────────────────────────────────────────

csv_buf = io.StringIO()
display.to_csv(csv_buf, index=False)

filename = (
    f"trace_treasury_{start}_{end}"
    f"{'_' + '_'.join(subtypes) if len(subtypes) < 4 else ''}.csv"
    .replace(" ", "_")
)

st.download_button(
    label="📥 Download CSV",
    data=csv_buf.getvalue(),
    file_name=filename,
    mime="text/csv",
    type="primary",
)

# ── Column descriptions ───────────────────────────────────────────────────────

with st.expander("Column descriptions"):
    st.markdown("""
| Column | Description |
|--------|-------------|
| `trade_date` | Trading date (YYYY-MM-DD) |
| `security_subtype` | Bills, FRN, Nominal Coupons, TIPS |
| `trading_category` | ATS & Interdealer, Dealer-to-Customer, Total |
| `maturity_bucket` | Remaining maturity range (Nominal Coupons and TIPS only) |
| `on_the_run` | On / Off the run (Nominal Coupons and TIPS only) |
| `volume_par` | Par-value trading volume (billions USD) |
| `trade_count` | Number of trades |
| `vwap` | Volume-weighted average price (on-the-run Nominal Coupons only) |
    """)
