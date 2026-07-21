"""Stooq EOD ingestor (quota-free bulk/daily primary).

Stooq serves per-symbol EOD CSV with no auth and no daily quota, which is why
it is the whole-universe primary. Known caveat: its CSV is split-adjusted but
NOT dividend-adjusted, so `adj_close` is left NULL here and total-return
adjustment is reconciled later against FMP's adjClose + logged actions.

Ticker convention is provider-specific and is exactly what the vertical-slice
run confirms empirically; `stooq_symbol` encodes the current best guess and the
slice reports which candidates actually returned data.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

BASE = "https://stooq.com/q/d/l/"
TIMEOUT = 30


def stooq_symbol(symbol: str, market: str) -> str:
    """Map an app symbol to a Stooq symbol. market in {'US','CA'}."""
    s = symbol.lower()
    if market == "US":
        return f"{s}.us"
    if market == "CA":
        # TSX names on Stooq: drop a trailing '.to' if present, add '.ca'
        s = s.removesuffix(".to")
        return f"{s}.ca"
    raise ValueError(f"Unknown market {market!r}")


def parse_stooq_csv(text: str) -> pd.DataFrame:
    """Parse a Stooq EOD CSV response into the canonical price frame.

    Raises ValueError on the sentinel bodies Stooq returns for bad symbols
    ('No data' / a bare header) so callers can log-and-skip rather than store junk.
    """
    stripped = text.strip()
    if not stripped or stripped.lower().startswith("no data"):
        raise ValueError("stooq: empty / 'No data' response")
    df = pd.read_csv(io.StringIO(text))
    if "Date" not in df.columns or "Close" not in df.columns:
        raise ValueError(f"stooq: unexpected columns {list(df.columns)}")
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce").astype("Int64")
    return df[["date", "open", "high", "low", "close", "volume"]]


def fetch_prices(symbol: str, market: str,
                 start: str = "2010-01-01", end: str | None = None) -> pd.DataFrame:
    """Fetch full EOD history for one symbol. Returns canonical frame + `symbol`."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    params = {
        "s": stooq_symbol(symbol, market),
        "d1": start.replace("-", ""),
        "d2": end.replace("-", ""),
        "i": "d",
    }
    r = requests.get(BASE, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    df = parse_stooq_csv(r.text)
    df.insert(0, "symbol", symbol)
    return df
