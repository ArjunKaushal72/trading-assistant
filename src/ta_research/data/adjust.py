"""Total-return adjustment from explicit corporate-action events.

Why this exists: the vertical slice proved neither free source hands us an
adjusted close (Stooq is split-adjusted only; FMP free omits adjClose). Momentum
and trend research on raw closes silently understates returns by the dividend
yield -- ~1.5-2%/yr on large caps, which is the same order as the edge we are
hunting. So we compute adjustment ourselves from dated dividend and split
events, which is also the point-in-time-honest route: every adjustment traces to
a dated event we stored, rather than to a vendor's silently-restated series.

Convention (standard back-adjustment): the LATEST bar is unadjusted, and all
PRIOR bars are scaled down by the cumulative factor of every event that happened
after them. Returns computed on adj_close are then total returns.

    For a dividend D paid with ex-date T on a prior close C_prev:
        factor_T = (C_prev - D) / C_prev
    For a split of ratio R (2-for-1 => R = 2) with ex-date T:
        factor_T = 1 / R

    adj_close(t) = close(t) * PRODUCT(factor_T for all events with T > t)

CRITICAL: `close` here must be the raw, unadjusted close. Feeding an
already-split-adjusted series (e.g. Stooq) together with split events would
double-apply the split. Callers must pass split events ONLY for sources whose
prices are raw. `apply_adjustments(..., splits_already_applied=True)` enforces
this by ignoring split events.
"""
from __future__ import annotations

import pandas as pd

PRICE_COLS = ["symbol", "date", "close"]


def _validate(prices: pd.DataFrame) -> None:
    missing = [c for c in PRICE_COLS if c not in prices.columns]
    if missing:
        raise ValueError(f"prices missing columns: {missing}")


def compute_adjustment_factors(
    prices: pd.DataFrame,
    dividends: pd.DataFrame | None = None,
    splits: pd.DataFrame | None = None,
    splits_already_applied: bool = False,
) -> pd.DataFrame:
    """Per symbol-date cumulative back-adjustment factor.

    prices:    columns [symbol, date, close]  (raw close)
    dividends: columns [symbol, ex_date, amount]
    splits:    columns [symbol, ex_date, ratio]   ratio 2.0 == 2-for-1
    Returns prices + [event_factor, cum_factor, adj_close].
    """
    _validate(prices)
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    df["event_factor"] = 1.0

    if dividends is not None and not dividends.empty:
        divs = dividends.copy()
        divs["ex_date"] = pd.to_datetime(divs["ex_date"])
        for _, ev in divs.iterrows():
            mask = (df["symbol"] == ev["symbol"]) & (df["date"] < ev["ex_date"])
            if not mask.any():
                continue
            prev_close = df.loc[mask, "close"].iloc[-1]
            if prev_close and prev_close > 0:
                f = (prev_close - float(ev["amount"])) / prev_close
                # the factor belongs to the ex-date bar itself
                on = (df["symbol"] == ev["symbol"]) & (df["date"] == ev["ex_date"])
                if on.any():
                    df.loc[on, "event_factor"] *= f

    if splits is not None and not splits.empty and not splits_already_applied:
        sp = splits.copy()
        sp["ex_date"] = pd.to_datetime(sp["ex_date"])
        for _, ev in sp.iterrows():
            on = (df["symbol"] == ev["symbol"]) & (df["date"] == ev["ex_date"])
            ratio = float(ev["ratio"])
            if on.any() and ratio > 0:
                df.loc[on, "event_factor"] *= 1.0 / ratio

    # cumulative product of all factors STRICTLY AFTER each bar, per symbol
    out = []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("date").copy()
        rev = g["event_factor"][::-1]
        # shift(1) so a bar is not adjusted by its own ex-date factor's ancestors
        cum_after = rev.cumprod().shift(1).fillna(1.0)[::-1]
        g["cum_factor"] = cum_after.values
        g["adj_close"] = g["close"] * g["cum_factor"]
        out.append(g)
    return pd.concat(out, ignore_index=True)


def apply_adjustments(
    prices: pd.DataFrame,
    dividends: pd.DataFrame | None = None,
    splits: pd.DataFrame | None = None,
    splits_already_applied: bool = False,
) -> pd.DataFrame:
    """Convenience wrapper returning [symbol, date, close, adj_close]."""
    res = compute_adjustment_factors(prices, dividends, splits, splits_already_applied)
    return res[["symbol", "date", "close", "adj_close", "cum_factor"]]


def total_return_series(adjusted: pd.DataFrame) -> pd.DataFrame:
    """Daily total returns from adj_close. Requires [symbol, date, adj_close]."""
    df = adjusted.sort_values(["symbol", "date"]).copy()
    df["ret"] = df.groupby("symbol")["adj_close"].pct_change()
    return df
