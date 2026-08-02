"""Data-quality gates for the price panel.

These are the 'green before any backtest result is believed' checks. Every
function is pure (no I/O, no network) and returns a QualityIssue list so the
caller can decide whether to fail the pipeline or quarantine rows.

Expected input schema (one row per symbol-day):
    columns: symbol, date, open, high, low, close, volume
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ta_research.data.calendars import missing_sessions

REQUIRED_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]


@dataclass
class QualityIssue:
    check: str
    detail: str
    offending: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)

    @property
    def n(self) -> int:
        return len(self.offending)


def _require_cols(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")


def check_no_duplicate_bars(df: pd.DataFrame) -> list[QualityIssue]:
    _require_cols(df)
    dup_mask = df.duplicated(subset=["symbol", "date"], keep=False)
    if dup_mask.any():
        return [QualityIssue("no_duplicate_bars",
                             f"{int(dup_mask.sum())} duplicate symbol-date rows",
                             df.loc[dup_mask])]
    return []


def check_ohlc_consistency(df: pd.DataFrame) -> list[QualityIssue]:
    _require_cols(df)
    o, h, lo, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    valid = (
        (lo <= h)
        & (lo <= o) & (o <= h)
        & (lo <= c) & (c <= h)
        & (v >= 0)
    )
    bad = df.loc[~valid]
    if len(bad):
        return [QualityIssue("ohlc_consistency",
                             f"{len(bad)} rows violate low<=open,close<=high or volume>=0",
                             bad)]
    return []


def check_calendar_gaps(df: pd.DataFrame, exchange: str) -> list[QualityIssue]:
    """Per-symbol: sessions the exchange was open but the symbol has no bar.

    Only flags gaps strictly inside each symbol's own [first, last] date range,
    so a symbol that listed late or delisted early is not falsely flagged.
    """
    _require_cols(df)
    issues: list[QualityIssue] = []
    for sym, g in df.groupby("symbol"):
        dts = pd.to_datetime(g["date"]).sort_values()
        gaps = missing_sessions(dts, exchange, dts.min(), dts.max())
        if len(gaps):
            issues.append(QualityIssue(
                "calendar_gaps",
                f"{sym}: {len(gaps)} missing sessions inside its listed range",
                pd.DataFrame({"symbol": sym, "missing_date": gaps}),
            ))
    return issues


def run_all(df: pd.DataFrame, exchange: str) -> list[QualityIssue]:
    return (
        check_no_duplicate_bars(df)
        + check_ohlc_consistency(df)
        + check_calendar_gaps(df, exchange)
    )
