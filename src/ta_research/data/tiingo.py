"""Tiingo EOD ingestor -- PRIMARY provider from 2026-07-23.

Chosen over FMP free (which whitelists symbols: 41 of 214) and over Twelve Data
(equal coverage, but more calls needed) for one structural reason: a single
daily-prices call returns raw OHLCV, adjusted OHLCV, AND the corporate-action
events (`divCash`, `splitFactor`) on every bar.

That matters beyond convenience. Because the events arrive alongside the
vendor's own adjClose, adjust.py can recompute the adjustment independently and
reconcile it against the vendor for EVERY symbol rather than a sample. A silent
vendor restatement then shows up as a divergence instead of quietly becoming
alpha in a backtest.

Free tier is rate-limited (roughly 50 requests/hour), so the backfill stays
resumable and quota-aware via budget.py. Key lives in Windows Credential Manager.
"""
from __future__ import annotations

import pandas as pd
import requests

from ta_research.data.symbols import to_tiingo

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None

BASE = "https://api.tiingo.com/tiingo/daily"
SERVICE = "trading-assistant"
KEY_USER = "tiingo_api_key"
TIMEOUT = 30


class TiingoError(RuntimeError):
    """Status travels as an attribute, never buried in a message string."""

    def __init__(self, status_code: int | None, message: str, body: str = ""):
        super().__init__(f"tiingo: HTTP {status_code} {message}")
        self.status_code = status_code
        self.body = body


def get_key() -> str | None:
    if keyring is None:
        return None
    return keyring.get_password(SERVICE, KEY_USER)


# ---------------------------------------------------------------- parsers ----

REQUIRED = {"date", "close"}


def parse_daily(payload: object, symbol: str) -> pd.DataFrame:
    """Canonical price frame with BOTH raw and vendor-adjusted closes."""
    if not isinstance(payload, list) or not payload:
        raise ValueError("tiingo: empty daily payload")
    df = pd.DataFrame(payload)
    if not REQUIRED.issubset(df.columns):
        raise ValueError(f"tiingo: unexpected keys {list(df.columns)}")
    out = pd.DataFrame({
        "symbol": symbol.upper(),
        "date": pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.date,
        "open": pd.to_numeric(df.get("open"), errors="coerce"),
        "high": pd.to_numeric(df.get("high"), errors="coerce"),
        "low": pd.to_numeric(df.get("low"), errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "adj_close": pd.to_numeric(df.get("adjClose"), errors="coerce"),
        "volume": pd.to_numeric(df.get("volume"), errors="coerce").astype("Int64"),
    })
    return out.sort_values("date").reset_index(drop=True)


def extract_dividends(payload: object, symbol: str) -> pd.DataFrame:
    """Dividend events from divCash. Zero-value bars are not events."""
    if not isinstance(payload, list) or not payload:
        return pd.DataFrame(columns=["symbol", "ex_date", "amount"])
    df = pd.DataFrame(payload)
    if "divCash" not in df.columns:
        return pd.DataFrame(columns=["symbol", "ex_date", "amount"])
    amt = pd.to_numeric(df["divCash"], errors="coerce").fillna(0.0)
    mask = amt > 0
    return pd.DataFrame({
        "symbol": symbol.upper(),
        "ex_date": pd.to_datetime(df.loc[mask, "date"], utc=True)
                     .dt.tz_localize(None).dt.date,
        "amount": amt[mask],
    }).reset_index(drop=True)


def extract_splits(payload: object, symbol: str) -> pd.DataFrame:
    """Split events from splitFactor. A factor of 1.0 means no split."""
    if not isinstance(payload, list) or not payload:
        return pd.DataFrame(columns=["symbol", "ex_date", "ratio"])
    df = pd.DataFrame(payload)
    if "splitFactor" not in df.columns:
        return pd.DataFrame(columns=["symbol", "ex_date", "ratio"])
    ratio = pd.to_numeric(df["splitFactor"], errors="coerce").fillna(1.0)
    mask = (ratio != 1.0) & (ratio > 0)
    return pd.DataFrame({
        "symbol": symbol.upper(),
        "ex_date": pd.to_datetime(df.loc[mask, "date"], utc=True)
                     .dt.tz_localize(None).dt.date,
        "ratio": ratio[mask],
    }).reset_index(drop=True)


# --------------------------------------------------------------- fetching ----

def fetch_daily_raw(symbol: str, start: str = "2010-01-01",
                    end: str | None = None, key: str | None = None) -> list:
    """One call -> everything. Returns the raw payload so all three parsers
    can run on it without re-fetching."""
    key = key or get_key()
    if not key:
        raise RuntimeError("tiingo: no API key in Credential Manager")
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    url = f"{BASE}/{to_tiingo(symbol)}/prices"
    try:
        r = requests.get(url, params={"startDate": start, "endDate": end,
                                      "token": key},
                         headers={"Content-Type": "application/json"},
                         timeout=TIMEOUT)
    except requests.RequestException as e:
        raise TiingoError(None, f"network error: {e!r}") from e
    if r.status_code != 200:
        raise TiingoError(r.status_code, r.text[:160], body=r.text[:400])
    payload = r.json()
    if not payload:
        # 200 + empty list is legitimate: the security did not exist in the
        # requested window (VOO before Sept 2010). Not a coverage failure.
        raise TiingoError(204, f"no rows in window for {symbol} "
                               f"(likely listed after {start})")
    return payload


def fetch_prices(symbol: str, start: str = "2010-01-01",
                 end: str | None = None, key: str | None = None) -> pd.DataFrame:
    return parse_daily(fetch_daily_raw(symbol, start, end, key), symbol)
