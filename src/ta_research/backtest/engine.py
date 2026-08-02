"""Daily target-weight backtest engine.

The charter names this "the highest-risk untested code" (§6) — so it is built
small, explicit, and test-first. It answers one question honestly: *if a strategy
had held these target weights, what would the after-cost, after-FX return in CAD
have been?* Making money on the market depends on that number not lying, so the
two properties that separate a real edge from a mirage are enforced here:

1. **No look-ahead.** Target weights decided using data through the close of day t
   are only earned from day t+`execution_lag` onward. Weights are held (forward-
   filled) across the session calendar and then shifted, so a signal can never
   capture the same day's return it was computed from.
2. **Costs and FX are always charged.** Commission (per side, ETF vs stock), a
   spread floor, slippage, and Norbert's-Gambit FX drag on USD names are deducted
   on every rebalance; USD symbols are converted to CAD at the daily FXUSDCAD rate
   so the equity curve is in the operator's home currency.

Returns-based (weight x return), not share-lot accounting: correct for weight
strategies, far less bug-prone, and exactly what the frozen Phase-1 pass/fail bar
consumes. Deliberately NOT modelled yet (charter TODOs): intraday fills, shorting/
borrow, options, delisting-to-recovery events, and drift-rebalancing cost.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import pandas as pd

from ta_research.backtest import metrics as _metrics
from ta_research.data.calendars import valid_sessions

_TOL = 1e-12


@dataclass(frozen=True)
class CostModel:
    """Transaction-cost assumptions, in the same units as ``config[costs]``."""

    commission_stock_per_side: float = 9.95
    commission_etf_buy: float = 0.0
    commission_etf_sell: float = 9.95
    spread_floor_bps: float = 5.0
    slippage_bps: float = 5.0
    fx_drag_bps: float = 15.0  # USD names only (Norbert's Gambit conversion)

    @classmethod
    def from_config(cls, cfg: dict) -> "CostModel":
        c = dict(cfg.get("costs", {}))
        return cls(
            commission_stock_per_side=float(c.get("commission_stock_per_side", 9.95)),
            commission_etf_buy=float(c.get("commission_etf_buy", 0.0)),
            commission_etf_sell=float(c.get("commission_etf_sell", 9.95)),
            spread_floor_bps=float(c.get("spread_floor_bps", 5.0)),
            slippage_bps=float(c.get("slippage_bps", 5.0)),
            fx_drag_bps=float(c.get("fx_drag_bps_ng", 15.0)),
        )

    def proportional_bps(self, is_usd: bool) -> float:
        """Proportional (bps) cost charged on traded notional per rebalance."""
        return self.spread_floor_bps + self.slippage_bps + (self.fx_drag_bps if is_usd else 0.0)

    def commission(self, side: str, is_etf: bool) -> float:
        if is_etf:
            return self.commission_etf_buy if side == "buy" else self.commission_etf_sell
        return self.commission_stock_per_side


@dataclass
class BacktestResult:
    equity: pd.Series          # CAD equity curve
    returns: pd.Series         # daily net returns (after costs, in CAD)
    weights: pd.DataFrame      # effective held weights (forward-filled + shifted)
    turnover: pd.Series        # daily one-sided turnover, sum|dW|
    costs: pd.Series           # daily total cost, in return units
    commissions: pd.Series     # daily commission paid, in currency
    metrics: dict


def _to_wide(obj: pd.DataFrame | pd.Series, value_col: str) -> pd.DataFrame:
    """Coerce a long [symbol, date, value] frame (or an already-wide frame) to
    a wide date-index x symbol-columns frame with a normalized DatetimeIndex."""
    if isinstance(obj, pd.Series):
        wide = obj.to_frame()
    elif isinstance(obj, pd.DataFrame) and {"symbol", "date"}.issubset(obj.columns):
        wide = obj.pivot_table(index="date", columns="symbol", values=value_col)
    else:
        wide = obj.copy()
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index)).normalize()
    return wide.sort_index()


def run_backtest(
    prices: pd.DataFrame | pd.Series,
    weights: pd.DataFrame | pd.Series,
    *,
    cost_model: CostModel | None = None,
    initial_capital: float = 25_000.0,
    price_col: str = "adj_close",
    execution_lag: int = 1,
    usd_symbols: set[str] | None = None,
    etf_symbols: set[str] | None = None,
    fx: pd.Series | None = None,
    calendar_exchange: str | None = "NYSE",
) -> BacktestResult:
    """Run a long-only daily target-weight backtest.

    ``prices``  : long [symbol, date, <price_col>] or wide (date index, symbol cols).
    ``weights`` : long [symbol, date, weight] or wide; the *target* weights decided
                  as of each date. Held (forward-filled) until the next target and
                  shifted by ``execution_lag`` so no signal earns its own day.
    ``fx``      : CAD per 1 USD, indexed by date. Applied only to ``usd_symbols``.
    """
    cost_model = cost_model or CostModel()
    usd_symbols = set(usd_symbols or [])
    etf_symbols = set(etf_symbols or [])

    px = _to_wide(prices, price_col)
    w_raw = _to_wide(weights, "weight")

    unknown = set(w_raw.columns) - set(px.columns)
    if unknown:
        warnings.warn(f"weights reference symbols with no prices: {sorted(unknown)}",
                      stacklevel=2)

    symbols = sorted(set(px.columns) | set(w_raw.columns))
    px = px.reindex(columns=symbols)

    # Session timeline: price dates, optionally restricted to real trading sessions.
    timeline = px.index.unique().sort_values()
    if calendar_exchange and len(timeline) > 0:
        sess = valid_sessions(calendar_exchange, timeline.min(), timeline.max())
        timeline = timeline.intersection(sess)
    px = px.reindex(timeline)

    # Native per-symbol returns from the (adjusted) price series.
    native_ret = px.pct_change(fill_method=None).fillna(0.0)

    # Convert USD names to CAD: (1 + r_native) * (fx_t / fx_{t-1}) - 1.
    cad_ret = native_ret.copy()
    if fx is not None and usd_symbols:
        fxs = fx.copy()
        fxs.index = pd.DatetimeIndex(pd.to_datetime(fxs.index)).normalize()
        fxs = fxs.reindex(timeline).ffill()
        fx_ret = fxs.pct_change(fill_method=None).fillna(0.0)
        for s in symbols:
            if s in usd_symbols:
                cad_ret[s] = (1.0 + native_ret[s]) * (1.0 + fx_ret) - 1.0

    # Effective held weights: hold the target (ffill), then shift for no-look-ahead.
    w_held = w_raw.reindex(columns=symbols).reindex(timeline).ffill().fillna(0.0)
    eff = w_held.shift(execution_lag).fillna(0.0)

    # Per-symbol change in held weight (turnover). First row is the move from flat.
    d_w = eff.diff()
    if len(eff) > 0:
        d_w.iloc[0] = eff.iloc[0]
    abs_dw = d_w.abs()
    turnover = abs_dw.sum(axis=1)

    # Proportional (bps) cost per symbol on traded notional.
    bps = pd.Series({s: cost_model.proportional_bps(s in usd_symbols) for s in symbols})
    prop_cost = abs_dw.mul(bps, axis=1).sum(axis=1) / 10_000.0

    # Gross portfolio return before costs.
    gross = (eff * cad_ret).sum(axis=1)

    # Walk the timeline to charge flat commissions against the running portfolio value.
    equity = pd.Series(index=timeline, dtype=float)
    returns = pd.Series(index=timeline, dtype=float)
    costs = pd.Series(index=timeline, dtype=float)
    commissions = pd.Series(0.0, index=timeline, dtype=float)
    pv = float(initial_capital)
    for d in timeline:
        comm = 0.0
        if pv > 0:
            row = d_w.loc[d]
            for s in symbols:
                delta = row[s]
                if abs(delta) > _TOL:
                    side = "buy" if delta > 0 else "sell"
                    comm += cost_model.commission(side, s in etf_symbols)
        comm_return = comm / pv if pv > 0 else 0.0
        day_cost = float(prop_cost.loc[d]) + comm_return
        net = float(gross.loc[d]) - day_cost
        pv = pv * (1.0 + net)
        equity.loc[d] = pv
        returns.loc[d] = net
        costs.loc[d] = day_cost
        commissions.loc[d] = comm

    summ = _metrics.summary(returns, equity, turnover=turnover)
    summ["n_trades"] = int((abs_dw > _TOL).to_numpy().sum())
    summ["commission_paid"] = float(commissions.sum())
    summ["final_equity"] = float(equity.iloc[-1]) if len(equity) else float(initial_capital)

    return BacktestResult(
        equity=equity, returns=returns, weights=eff, turnover=turnover,
        costs=costs, commissions=commissions, metrics=summ,
    )
