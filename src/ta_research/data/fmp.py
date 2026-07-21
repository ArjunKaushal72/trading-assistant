"""FMP ingestor -- STABLE endpoints only (v3 legacy is dead for post-2025 keys).

Confirmed by the capability probe: `stable/historical-price-eod/full` returns
adjusted OHLCV back to 2010 on the free tier. Rate limits and CA coverage are
measured by the vertical slice before FMP is relied on beyond cross-checking.
Key is read from Windows Credential Manager via keyring -- never from a file.
"""
from __future__ import annotations

import pandas as pd
import requests

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None

STABLE_EOD = "https://financialmodelingprep.com/stable/historical-price-eod/full"
SERVICE = "trading-assistant"
KEY_USER = "fmp_api_key"
TIMEOUT = 30


def get_key() -> str | None:
    if keyring is None:
        return None
    return keyring.get_password(SERVICE, KEY_USER)


def parse_stable_eod(payload: object) -> pd.DataFrame:
    """Parse the stable EOD payload (list, or dict wrapping a list) to canonical frame.

    Tolerant of adjusted-close field naming across FMP versions.
    """
    if isinstance(payload, dict):
        series = payload.get("historical") or payload.get("results") or payload.get("data") or []
    elif isinstance(payload, list):
        series = payload
    else:
        series = []
    if not series:
        raise ValueError("fmp: empty series")
    df = pd.DataFrame(series)
    if "date" not in df.columns or "close" not in df.columns:
        raise ValueError(f"fmp: unexpected keys {list(df.columns)}")
    adj_col = next((c for c in ("adjClose", "adjclose", "adj_close") if c in df.columns), None)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.date,
        "open": pd.to_numeric(df.get("open"), errors="coerce"),
        "high": pd.to_numeric(df.get("high"), errors="coerce"),
        "low": pd.to_numeric(df.get("low"), errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "adj_close": pd.to_numeric(df[adj_col], errors="coerce") if adj_col else pd.NA,
        "volume": pd.to_numeric(df.get("volume"), errors="coerce").astype("Int64"),
    })
    return out


def fetch_prices(symbol: str, start: str = "2010-01-01",
                 end: str | None = None, key: str | None = None) -> pd.DataFrame:
    key = key or get_key()
    if not key:
        raise RuntimeError("fmp: no API key in Credential Manager")
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    r = requests.get(STABLE_EOD, params={
        "symbol": symbol, "from": start, "to": end, "apikey": key,
    }, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"fmp: HTTP {r.status_code} {r.text[:120]}")
    df = parse_stable_eod(r.json())
    df.insert(0, "symbol", symbol)
    return df
