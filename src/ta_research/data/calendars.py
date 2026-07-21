"""Exchange trading calendars for the CA/US universe.

Uses pandas_market_calendars so TSX (XTSE) and NYSE (XNYS) holidays and
half-days are handled correctly. Mismatched calendars between the two
exchanges are a classic silent join bug for a mixed CA/US book; this module
is the single source of truth for 'was this a trading session?'.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pandas_market_calendars as mcal

EXCHANGE_CODES = {"TSX": "XTSE", "NYSE": "XNYS"}


def valid_sessions(exchange: str, start: str | date, end: str | date) -> pd.DatetimeIndex:
    """Return tz-naive DatetimeIndex of valid trading sessions (inclusive)."""
    if exchange not in EXCHANGE_CODES:
        raise ValueError(f"Unknown exchange {exchange!r}; expected one of {list(EXCHANGE_CODES)}")
    cal = mcal.get_calendar(EXCHANGE_CODES[exchange])
    sessions = cal.valid_days(start_date=str(start), end_date=str(end))
    return pd.DatetimeIndex(sessions.tz_localize(None).normalize())


def missing_sessions(
    dates_present: pd.Series | pd.DatetimeIndex,
    exchange: str,
    start: str | date,
    end: str | date,
) -> pd.DatetimeIndex:
    """Sessions the exchange was open but which are absent from `dates_present`."""
    present = pd.DatetimeIndex(pd.to_datetime(pd.Index(dates_present)).normalize()).unique()
    expected = valid_sessions(exchange, start, end)
    return expected.difference(present)
