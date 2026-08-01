"""Stooq EOD ingestor (quota-free bulk/daily primary).

Stooq serves per-symbol EOD CSV with no auth and no daily quota. It rejects
requests that look automated: a bare python-requests User-Agent gets HTTP 404
(NOT the 'No data' sentinel a genuinely-bad symbol returns), which is what the
first vertical-slice run hit. We therefore send a browser UA and expose the
request variants so `scripts/source_diag.py` can prove empirically which
combination this network actually gets served.

Caveat kept from before: Stooq CSV is split-adjusted but NOT dividend-adjusted,
so `adj_close` stays NULL here; total-return adjustment is built in adjust.py
from explicit dividend/split events.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

HOSTS = ["https://stooq.com/q/d/l/", "https://stooq.pl/q/d/l/"]
TIMEOUT = 30

# A browser UA is required; Stooq 404s obvious bots.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def stooq_symbol(symbol: str, market: str) -> str:
    """Map an app symbol to a Stooq symbol. market in {'US','CA'}."""
    s = symbol.lower()
    if market == "US":
        return f"{s}.us"
    if market == "CA":
        s = s.removesuffix(".to")
        return f"{s}.ca"
    raise ValueError(f"Unknown market {market!r}")


def parse_stooq_csv(text: str) -> pd.DataFrame:
    """Parse a Stooq EOD CSV response into the canonical price frame.

    Raises ValueError on the sentinel bodies Stooq returns for bad symbols
    ('No data' / bare header) so callers log-and-skip rather than store junk.
    """
    stripped = text.strip()
    if not stripped or stripped.lower().startswith("no data"):
        raise ValueError("stooq: empty / 'No data' response")
    if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
        raise ValueError("stooq: HTML returned (blocked or rate-limited), not CSV")
    df = pd.read_csv(io.StringIO(text))
    if "Date" not in df.columns or "Close" not in df.columns:
        raise ValueError(f"stooq: unexpected columns {list(df.columns)}")
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    if df.empty:
        raise ValueError("stooq: header only, no rows")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce").astype("Int64")
    return df[["date", "open", "high", "low", "close", "volume"]]


def build_url_params(stooq_sym: str, start: str | None, end: str | None) -> dict:
    """Query params. Date range is optional -- omitting it is a fallback variant."""
    params = {"s": stooq_sym, "i": "d"}
    if start and end:
        params["d1"] = start.replace("-", "")
        params["d2"] = end.replace("-", "")
    return params


def fetch_prices(symbol: str, market: str,
                 start: str | None = "2010-01-01", end: str | None = None,
                 host: str = HOSTS[0], use_dates: bool = True) -> pd.DataFrame:
    """Fetch full EOD history for one symbol. Returns canonical frame + `symbol`."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    params = build_url_params(stooq_symbol(symbol, market),
                              start if use_dates else None,
                              end if use_dates else None)
    r = requests.get(host, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    df = parse_stooq_csv(r.text)
    df.insert(0, "symbol", symbol)
    return df
