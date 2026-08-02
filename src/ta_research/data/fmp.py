"""FMP ingestor -- STABLE endpoints only (v3 legacy is dead for post-2025 keys).

Empirically confirmed FREE on this key (source_diag 2026-07-23):
  * historical-price-eod/full               raw OHLCV, 2010-> , NO adjusted close
  * historical-price-eod/dividend-adjusted  adjOpen/High/Low/Close  <- total return
  * dividends                               dated dividend events
  * splits                                  numerator/denominator events
Confirmed NOT available: any `.TO` (Canadian) symbol -> HTTP 402 premium.

Both a vendor-adjusted series AND the raw+events needed to rebuild it ourselves
are therefore free. We ingest both on purpose: adjust.py recomputes adjustment
from events and we reconcile against FMP's adjClose. A silent vendor restatement
then shows up as a divergence instead of as alpha.

Key is read from Windows Credential Manager via keyring -- never from a file.
"""
from __future__ import annotations

import pandas as pd
import requests

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]

BASE = "https://financialmodelingprep.com/stable"
STABLE_EOD = f"{BASE}/historical-price-eod/full"
STABLE_EOD_ADJ = f"{BASE}/historical-price-eod/dividend-adjusted"
STABLE_DIVIDENDS = f"{BASE}/dividends"
STABLE_SPLITS = f"{BASE}/splits"

SERVICE = "trading-assistant"
KEY_USER = "fmp_api_key"
TIMEOUT = 30


def get_key() -> str | None:
    if keyring is None:
        return None
    return keyring.get_password(SERVICE, KEY_USER)


def _as_list(payload: object) -> list:
    if isinstance(payload, dict):
        for k in ("historical", "results", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
        return []
    return payload if isinstance(payload, list) else []


class FMPError(RuntimeError):
    """Carries the HTTP status as DATA, not buried in a message string.

    Root cause of a real incident: the previous version raised
    PermissionError("fmp: premium-only ...") for HTTP 402, and the caller
    recovered the status by substring-searching the message. "402" never
    appeared in that string, so 173 premium-restricted symbols were reported as
    unclassified "hard" failures and their true cause stayed invisible. Status
    codes now travel as an attribute.
    """

    def __init__(self, status_code: int | None, message: str, body: str = ""):
        super().__init__(f"fmp: HTTP {status_code} {message}")
        self.status_code = status_code
        self.body = body


def _get(url: str, params: dict) -> object:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise FMPError(None, f"network error: {e!r}") from e
    if r.status_code != 200:
        raise FMPError(r.status_code, r.text[:160], body=r.text[:400])
    return r.json()


# ---------------------------------------------------------------- parsers ----

def parse_stable_eod(payload: object) -> pd.DataFrame:
    """Raw OHLCV. Confirmed keys: symbol,date,open,high,low,close,volume,change,
    changePercent,vwap -- note there is NO adjusted close here."""
    series = _as_list(payload)
    if not series:
        raise ValueError("fmp: empty series")
    df = pd.DataFrame(series)
    if "date" not in df.columns or "close" not in df.columns:
        raise ValueError(f"fmp: unexpected keys {list(df.columns)}")
    return pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.date,
        "open": pd.to_numeric(df.get("open"), errors="coerce"),
        "high": pd.to_numeric(df.get("high"), errors="coerce"),
        "low": pd.to_numeric(df.get("low"), errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "adj_close": pd.NA,
        "volume": pd.to_numeric(df.get("volume"), errors="coerce").astype("Int64"),
    })


def parse_stable_eod_adjusted(payload: object) -> pd.DataFrame:
    """Dividend-adjusted OHLCV. Confirmed keys: adjOpen/adjHigh/adjLow/adjClose."""
    series = _as_list(payload)
    if not series:
        raise ValueError("fmp: empty adjusted series")
    df = pd.DataFrame(series)
    if "adjClose" not in df.columns:
        raise ValueError(f"fmp: no adjClose in {list(df.columns)}")
    return pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.date,
        "open": pd.to_numeric(df.get("adjOpen"), errors="coerce"),
        "high": pd.to_numeric(df.get("adjHigh"), errors="coerce"),
        "low": pd.to_numeric(df.get("adjLow"), errors="coerce"),
        "close": pd.to_numeric(df["adjClose"], errors="coerce"),
        "adj_close": pd.to_numeric(df["adjClose"], errors="coerce"),
        "volume": pd.to_numeric(df.get("volume"), errors="coerce").astype("Int64"),
    })


DIV_AMOUNT_KEYS = ("dividend", "adjDividend", "amount", "value")


def parse_dividends(payload: object) -> pd.DataFrame:
    """Dated dividend events -> [symbol, ex_date, amount, declaration_date].

    `date` is the ex-dividend date, which is the correct date for back-adjustment.
    declaration_date is retained because it -- not ex-date -- is when the payout
    became knowable, which matters for point-in-time feature construction later.
    """
    series = _as_list(payload)
    if not series:
        raise ValueError("fmp: empty dividends")
    df = pd.DataFrame(series)
    amt_col = next((c for c in DIV_AMOUNT_KEYS if c in df.columns), None)
    if amt_col is None:
        raise ValueError(f"fmp: no dividend amount key in {list(df.columns)}")
    out = pd.DataFrame({
        "symbol": df.get("symbol"),
        "ex_date": pd.to_datetime(df["date"]).dt.date,
        "amount": pd.to_numeric(df[amt_col], errors="coerce"),
    })
    if "declarationDate" in df.columns:
        out["declaration_date"] = pd.to_datetime(
            df["declarationDate"], errors="coerce").dt.date
    return out.dropna(subset=["amount"])


def parse_splits(payload: object) -> pd.DataFrame:
    """Split events -> [symbol, ex_date, ratio].

    FMP reports numerator/denominator (AAPL 2020-08-31 => 4/1). ratio > 1 means
    more shares at a lower price, so adjust.py scales prior bars by 1/ratio.
    """
    series = _as_list(payload)
    if not series:
        raise ValueError("fmp: empty splits")
    df = pd.DataFrame(series)
    if {"numerator", "denominator"}.issubset(df.columns):
        num = pd.to_numeric(df["numerator"], errors="coerce")
        den = pd.to_numeric(df["denominator"], errors="coerce").replace(0, pd.NA)
        ratio = num / den
    elif "ratio" in df.columns:
        ratio = pd.to_numeric(df["ratio"], errors="coerce")
    else:
        raise ValueError(f"fmp: no split ratio keys in {list(df.columns)}")
    return pd.DataFrame({
        "symbol": df.get("symbol"),
        "ex_date": pd.to_datetime(df["date"]).dt.date,
        "ratio": ratio,
    }).dropna(subset=["ratio"])


# --------------------------------------------------------------- fetchers ----

def _fetch(url: str, symbol: str, key: str | None,
           start: str | None, end: str | None, parser) -> pd.DataFrame:
    key = key or get_key()
    if not key:
        raise RuntimeError("fmp: no API key in Credential Manager")
    params = {"symbol": symbol, "apikey": key}
    if start:
        params["from"] = start
        params["to"] = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    df = parser(_get(url, params))
    if "symbol" not in df.columns:
        df.insert(0, "symbol", symbol)
    return df


def fetch_prices(symbol, start="2010-01-01", end=None, key=None):
    return _fetch(STABLE_EOD, symbol, key, start, end, parse_stable_eod)


def fetch_prices_adjusted(symbol, start="2010-01-01", end=None, key=None):
    return _fetch(STABLE_EOD_ADJ, symbol, key, start, end, parse_stable_eod_adjusted)


def fetch_dividends(symbol, key=None):
    return _fetch(STABLE_DIVIDENDS, symbol, key, None, None, parse_dividends)


def fetch_splits(symbol, key=None):
    return _fetch(STABLE_SPLITS, symbol, key, None, None, parse_splits)
