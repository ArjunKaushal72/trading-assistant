"""Performance metrics for a backtest return stream.

Deliberately plain and auditable — every number here feeds the frozen Phase-1
pass/fail bar (`CHARTER.md` amendments), so each is computed one obvious way and
tested against hand-worked values. Returns are simple (not log) daily returns;
the risk-free rate defaults to 0 (documented approximation — swap in a T-bill
series when the bar is evaluated for real). Nothing here annualizes by guessing:
`periods_per_year` is explicit and defaults to 252 trading days.

NOT computed yet (charter TODOs): deflated Sharpe (needs the number of trials),
and perturbation stability — both belong to the statistics layer that guards
against data-snooping, not to this mechanical summary.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def total_return(returns: pd.Series) -> float:
    """Compounded total return over the whole series."""
    if len(returns) == 0:
        return 0.0
    return float((1.0 + returns).prod() - 1.0)


def cagr(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Compound annual growth rate implied by the return stream."""
    n = len(returns)
    if n == 0:
        return 0.0
    growth = float((1.0 + returns).prod())
    if growth <= 0.0:
        return -1.0
    years = n / periods_per_year
    if years <= 0:
        return 0.0
    return growth ** (1.0 / years) - 1.0


def ann_volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Annualized standard deviation of returns."""
    if len(returns) < 2:
        return 0.0
    sd = float(returns.std(ddof=1))
    return sd * np.sqrt(periods_per_year)


def sharpe(returns: pd.Series, rf: float = 0.0,
           periods_per_year: int = TRADING_DAYS) -> float:
    """Annualized Sharpe ratio. rf is the per-period risk-free rate (default 0)."""
    if len(returns) < 2:
        return 0.0
    excess = returns - rf
    sd = float(excess.std(ddof=1))
    if sd == 0.0:
        return 0.0
    return float(excess.mean() / sd) * np.sqrt(periods_per_year)


def equity_curve(returns: pd.Series, initial: float = 1.0) -> pd.Series:
    """Cumulative equity from a return stream."""
    return initial * (1.0 + returns).cumprod()


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline, as a negative fraction (0 if none)."""
    if len(equity) == 0:
        return 0.0
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def calmar(returns: pd.Series, equity: pd.Series,
           periods_per_year: int = TRADING_DAYS) -> float:
    """CAGR divided by the magnitude of max drawdown."""
    mdd = max_drawdown(equity)
    if mdd == 0.0:
        return float("nan")
    return cagr(returns, periods_per_year) / abs(mdd)


def summary(returns: pd.Series, equity: pd.Series | None = None,
            turnover: pd.Series | None = None,
            periods_per_year: int = TRADING_DAYS) -> dict:
    """One dict of every metric the frozen pass/fail bar needs."""
    returns = returns.dropna()
    if equity is None:
        equity = equity_curve(returns)
    n = len(returns)
    years = n / periods_per_year if periods_per_year else 0.0
    out = {
        "n_periods": int(n),
        "years": float(years),
        "total_return": total_return(returns),
        "cagr": cagr(returns, periods_per_year),
        "ann_volatility": ann_volatility(returns, periods_per_year),
        "sharpe": sharpe(returns, 0.0, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "calmar": calmar(returns, equity, periods_per_year),
    }
    out["max_drawdown_pct"] = abs(out["max_drawdown"])
    if turnover is not None and years > 0:
        out["avg_daily_turnover"] = float(turnover.mean())
        out["turnover_per_year"] = float(turnover.sum() / years)
    return out
