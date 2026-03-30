"""
Turso database layer using the HTTP pipeline API.
Handles both Streamlit Cloud (st.secrets) and CLI/GitHub Actions (os.environ) auth.
"""
import os
from typing import Optional

import pandas as pd
import requests as http

# ── Config ────────────────────────────────────────────────────────────────────

def _get_config() -> tuple[str, str]:
    try:
        import streamlit as st
        return st.secrets["TURSO_DATABASE_URL"], st.secrets["TURSO_AUTH_TOKEN"]
    except Exception:
        return os.environ["TURSO_DATABASE_URL"], os.environ["TURSO_AUTH_TOKEN"]


def _http_url(libsql_url: str) -> str:
    return (
        libsql_url
        .replace("libsql://", "https://")
        .replace("wss://", "https://")
        .replace("ws://", "http://")
    )


# ── Low-level API ─────────────────────────────────────────────────────────────

def _make_arg(value) -> dict:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if value != value:  # NaN
            return {"type": "null"}
        return {"type": "real", "value": str(value)}
    return {"type": "text", "value": str(value)}


def _pipeline(stmts: list[dict]) -> list[dict]:
    url, token = _get_config()
    pipeline_reqs = [{"type": "execute", "stmt": s} for s in stmts]
    pipeline_reqs.append({"type": "close"})

    resp = http.post(
        f"{_http_url(url)}/v2/pipeline",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": pipeline_reqs},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for r in data["results"][:-1]:  # exclude close
        if r["type"] == "error":
            raise RuntimeError(f"Turso SQL error: {r['error']['message']}")
        results.append(r["response"]["result"])
    return results


def execute(sql: str, args: list = None) -> dict:
    stmt: dict = {"sql": sql}
    if args:
        stmt["args"] = [_make_arg(a) for a in args]
    return _pipeline([stmt])[0]


def execute_batch(statements: list[tuple]) -> None:
    """Execute a list of (sql, args) tuples in a single pipeline call."""
    stmts = []
    for sql, args in statements:
        stmt: dict = {"sql": sql}
        if args:
            stmt["args"] = [_make_arg(a) for a in args]
        stmts.append(stmt)
    _pipeline(stmts)


def _result_to_df(result: dict) -> pd.DataFrame:
    if not result.get("cols"):
        return pd.DataFrame()
    cols = [c["name"] for c in result["cols"]]
    rows = [
        [v.get("value") if v["type"] != "null" else None for v in row]
        for row in result["rows"]
    ]
    return pd.DataFrame(rows, columns=cols)


# ── Schema ────────────────────────────────────────────────────────────────────

# on_the_run: '' = N/A (Bills, FRN), 'On' = on-the-run, 'Off' = off-the-run
# maturity_bucket: '' = N/A (Bills, FRN), otherwise e.g. '<=2Y', '>2-3Y'

def init_db() -> None:
    execute("""
        CREATE TABLE IF NOT EXISTS treasury_daily (
            trade_date       TEXT    NOT NULL,
            security_subtype TEXT    NOT NULL,
            trading_category TEXT    NOT NULL,
            maturity_bucket  TEXT    NOT NULL DEFAULT '',
            on_the_run       TEXT    NOT NULL DEFAULT '',
            volume_par       REAL,
            trade_count      INTEGER,
            vwap             REAL,
            PRIMARY KEY (trade_date, security_subtype, trading_category,
                         maturity_bucket, on_the_run)
        )
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_td ON treasury_daily(trade_date)")
    execute("CREATE INDEX IF NOT EXISTS idx_subtype ON treasury_daily(security_subtype)")


# ── Write ─────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT OR REPLACE INTO treasury_daily
        (trade_date, security_subtype, trading_category, maturity_bucket,
         on_the_run, volume_par, trade_count, vwap)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def upsert_records(records: list[dict]) -> int:
    if not records:
        return 0
    stmts = [
        (UPSERT_SQL, [
            r["trade_date"],
            r["security_subtype"],
            r["trading_category"],
            r.get("maturity_bucket") or "",
            r.get("on_the_run") or "",
            r.get("volume_par"),
            r.get("trade_count"),
            r.get("vwap"),
        ])
        for r in records
    ]
    # Batch in chunks of 100 to stay under request-body limits
    for i in range(0, len(stmts), 100):
        execute_batch(stmts[i : i + 100])
    return len(records)


# ── Read ──────────────────────────────────────────────────────────────────────

def get_latest_date() -> Optional[str]:
    result = execute("SELECT MAX(trade_date) AS max_date FROM treasury_daily")
    if result["rows"] and result["rows"][0][0]["type"] != "null":
        return result["rows"][0][0]["value"]
    return None


def get_data(
    start_date: str = None,
    end_date: str = None,
    security_subtypes: list = None,
    trading_categories: list = None,
) -> pd.DataFrame:
    conditions, args = [], []

    if start_date:
        conditions.append("trade_date >= ?")
        args.append(start_date)
    if end_date:
        conditions.append("trade_date <= ?")
        args.append(end_date)
    if security_subtypes:
        ph = ",".join("?" * len(security_subtypes))
        conditions.append(f"security_subtype IN ({ph})")
        args.extend(security_subtypes)
    if trading_categories:
        ph = ",".join("?" * len(trading_categories))
        conditions.append(f"trading_category IN ({ph})")
        args.extend(trading_categories)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM treasury_daily {where} ORDER BY trade_date ASC"

    result = execute(sql, args or None)
    df = _result_to_df(result)

    if df.empty:
        return df

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ("volume_par", "trade_count", "vwap"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Expose on_the_run as a readable label; '' → None
    df["on_the_run"] = df["on_the_run"].replace("", None)
    df["maturity_bucket"] = df["maturity_bucket"].replace("", None)

    return df


def get_distinct_values(column: str) -> list:
    allowed = {"security_subtype", "trading_category", "maturity_bucket", "on_the_run"}
    if column not in allowed:
        raise ValueError(f"Column '{column}' not allowed for distinct query")
    result = execute(
        f"SELECT DISTINCT {column} FROM treasury_daily WHERE {column} != '' ORDER BY {column}"
    )
    return [r[0]["value"] for r in result["rows"] if r[0]["type"] != "null"]
