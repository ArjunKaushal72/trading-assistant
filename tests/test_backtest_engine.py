"""Backtest engine correctness suite.

The charter calls this the highest-risk untested code, so the tests target the
exact ways a backtest lies: look-ahead, un-charged costs, ignored FX, and
fabricated returns for names that stopped trading.
"""
import numpy as np
import pandas as pd
import pytest

from ta_research.backtest.engine import CostModel, run_backtest
from ta_research.data.calendars import valid_sessions

ZERO = CostModel(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _sessions(n: int):
    return valid_sessions("NYSE", "2023-01-03", "2023-04-01")[:n]


def _prices(symbol, sessions, closes):
    return pd.DataFrame({"symbol": symbol, "date": sessions, "adj_close": closes})


def _weights(symbol, sessions, w):
    return pd.DataFrame({"symbol": symbol, "date": sessions, "weight": w})


def test_no_lookahead_same_day_jump_is_not_captured():
    sess = _sessions(6)
    closes = [100, 100, 100, 110, 110, 110]  # +10% return lands on index 3

    # Decide the day BEFORE the jump -> the jump is legitimately earned.
    r_before = run_backtest(_prices("AAA", sess, closes),
                            _weights("AAA", sess, [0, 0, 1, 1, 1, 1]),
                            cost_model=ZERO)
    assert r_before.metrics["total_return"] == pytest.approx(0.10, abs=1e-9)

    # Turn the weight on ONLY on the jump day (look-ahead cheat) -> earns nothing,
    # because the decision is shifted one session and the jump has already happened.
    r_cheat = run_backtest(_prices("AAA", sess, closes),
                           _weights("AAA", sess, [0, 0, 0, 1, 0, 0]),
                           cost_model=ZERO)
    assert r_cheat.metrics["total_return"] == pytest.approx(0.0, abs=1e-9)
    assert r_before.metrics["total_return"] > r_cheat.metrics["total_return"]


def test_costs_are_charged_exactly_on_entry():
    sess = _sessions(4)
    prices = _prices("AAA", sess, [100, 100, 100, 100])          # flat market
    weights = _weights("AAA", sess, [1, 1, 1, 1])
    cm = CostModel(commission_stock_per_side=9.95, commission_etf_buy=0.0,
                   commission_etf_sell=9.95, spread_floor_bps=5.0,
                   slippage_bps=5.0, fx_drag_bps=0.0)
    init = 25_000.0
    r = run_backtest(prices, weights, cost_model=cm, initial_capital=init)

    entry_cost = 0.001 + 9.95 / init         # 10 bps proportional + one commission
    assert r.metrics["final_equity"] == pytest.approx(init * (1 - entry_cost), rel=1e-12)
    assert r.metrics["commission_paid"] == pytest.approx(9.95, rel=1e-12)
    assert r.metrics["n_trades"] == 1


def test_zero_cost_flat_market_preserves_capital():
    sess = _sessions(5)
    r = run_backtest(_prices("AAA", sess, [100] * 5),
                     _weights("AAA", sess, [1, 1, 1, 1, 1]),
                     cost_model=ZERO, initial_capital=10_000.0)
    assert r.metrics["final_equity"] == pytest.approx(10_000.0, rel=1e-12)


def test_fx_moves_cad_equity_for_usd_names():
    sess = _sessions(4)
    prices = _prices("AAA", sess, [50, 50, 50, 50])              # USD price flat
    weights = _weights("AAA", sess, [1, 1, 1, 1])
    fx = pd.Series([1.30, 1.30, 1.365, 1.365], index=sess)       # +5% on index 2
    r = run_backtest(prices, weights, cost_model=ZERO,
                     usd_symbols={"AAA"}, fx=fx)
    assert r.metrics["total_return"] == pytest.approx(0.05, abs=1e-9)


def test_fx_not_applied_to_non_usd_symbol():
    sess = _sessions(4)
    prices = _prices("CAA", sess, [50, 50, 50, 50])
    weights = _weights("CAA", sess, [1, 1, 1, 1])
    fx = pd.Series([1.30, 1.30, 1.365, 1.365], index=sess)
    r = run_backtest(prices, weights, cost_model=ZERO,
                     usd_symbols={"AAA"}, fx=fx)                 # CAA not USD
    assert r.metrics["total_return"] == pytest.approx(0.0, abs=1e-9)


def test_missing_price_after_delisting_is_not_fabricated():
    sess = _sessions(6)
    aaa = _prices("AAA", sess, [100, 101, 102, 103, 104, 105])
    bbb = _prices("BBB", sess, [100, 100, 100, np.nan, np.nan, np.nan])
    prices = pd.concat([aaa, bbb], ignore_index=True)
    weights = pd.concat([_weights("AAA", sess, [0.5] * 6),
                         _weights("BBB", sess, [0.5] * 6)], ignore_index=True)
    r = run_backtest(prices, weights, cost_model=ZERO)
    assert np.isfinite(r.equity.to_numpy()).all()
    assert not r.returns.isna().any()


def test_target_weight_held_between_decision_dates():
    sess = _sessions(6)
    prices = _prices("AAA", sess, [100] * 6)
    weights = pd.DataFrame({"symbol": ["AAA"], "date": [sess[0]], "weight": [1.0]})
    r = run_backtest(prices, weights, cost_model=ZERO)
    assert r.weights["AAA"].iloc[0] == 0.0            # not yet held on day 0 (lag)
    assert (r.weights["AAA"].iloc[1:] == 1.0).all()   # held forward thereafter


def test_costmodel_from_config():
    cfg = {"costs": {"commission_stock_per_side": 4.95, "spread_floor_bps": 3,
                     "slippage_bps": 2, "fx_drag_bps_ng": 20}}
    cm = CostModel.from_config(cfg)
    assert cm.commission_stock_per_side == 4.95
    assert cm.proportional_bps(is_usd=True) == pytest.approx(25.0)
    assert cm.proportional_bps(is_usd=False) == pytest.approx(5.0)
