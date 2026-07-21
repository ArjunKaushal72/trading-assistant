"""DuckDB local store: schema + idempotent writers.

The store is the single source of truth for the research phase. Writers are
idempotent (safe to re-run the same day) via ON CONFLICT, and every row carries
`source` and `ingested_at` so the point-in-time habit is inherited from day one.
`prices_raw` keeps (symbol, date, source) as PK so Stooq and FMP bars coexist
for cross-checking rather than one silently overwriting the other.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_raw (
    symbol      VARCHAR NOT NULL,
    date        DATE    NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    adj_close   DOUBLE,
    volume      BIGINT,
    source      VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, date, source)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    date        DATE    NOT NULL,
    series      VARCHAR NOT NULL,   -- e.g. FXUSDCAD (CAD per 1 USD)
    rate        DOUBLE,
    source      VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL,
    PRIMARY KEY (date, series)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    run_ts      TIMESTAMP NOT NULL,
    source      VARCHAR NOT NULL,
    symbol      VARCHAR,
    rows        BIGINT,
    ok          BOOLEAN,
    note        VARCHAR
);
"""

PRICE_COLS = ["symbol", "date", "open", "high", "low", "close",
              "adj_close", "volume", "source", "ingested_at"]


def connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute(SCHEMA)
    return con


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upsert_prices(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, source: str) -> int:
    """Idempotent write of a price frame for one source. Returns rows written."""
    if df.empty:
        return 0
    frame = df.copy()
    frame["source"] = source
    frame["ingested_at"] = _now()
    if "adj_close" not in frame.columns:
        frame["adj_close"] = pd.NA
    frame = frame[PRICE_COLS]
    con.register("tmp_prices", frame)
    con.execute("""
        INSERT INTO prices_raw
        SELECT * FROM tmp_prices
        ON CONFLICT (symbol, date, source) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, adj_close=excluded.adj_close,
            volume=excluded.volume, ingested_at=excluded.ingested_at;
    """)
    con.unregister("tmp_prices")
    return len(frame)


def upsert_fx(con: duckdb.DuckDBPyConnection, df: pd.DataFrame,
              series: str, source: str) -> int:
    """Idempotent write of an FX series. df needs columns [date, rate]."""
    if df.empty:
        return 0
    frame = df[["date", "rate"]].copy()
    frame["series"] = series
    frame["source"] = source
    frame["ingested_at"] = _now()
    frame = frame[["date", "series", "rate", "source", "ingested_at"]]
    con.register("tmp_fx", frame)
    con.execute("""
        INSERT INTO fx_rates
        SELECT * FROM tmp_fx
        ON CONFLICT (date, series) DO UPDATE SET
            rate=excluded.rate, source=excluded.source,
            ingested_at=excluded.ingested_at;
    """)
    con.unregister("tmp_fx")
    return len(frame)


def log_ingest(con, source: str, symbol: str | None, rows: int,
               ok: bool, note: str = "") -> None:
    con.execute(
        "INSERT INTO ingest_log VALUES (?,?,?,?,?,?)",
        [_now(), source, symbol, rows, ok, note],
    )
