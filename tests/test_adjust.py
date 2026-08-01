"""Known-answer tests for total-return adjustment.

These are hand-computable by design: if the arithmetic here is wrong, every
momentum/trend signal downstream is wrong in a way no backtest would reveal.
"""
import pandas as pd
import pytest

from ta_research.data import adjust


def _prices(closes, dates=None, symbol="AAA"):
    dates = dates or pd.bdate_range("2023-01-02", periods=len(closes))
    return pd.DataFrame({"symbol": symbol, "date": dates, "close": closes})


def test_no_events_adj_equals_close():
    p = _prices([10.0, 11.0, 12.0])
    out = adjust.apply_adjustments(p)
    assert (out["adj_close"] == out["close"]).all()
    assert (out["cum_factor"] == 1.0).all()


def test_latest_bar_is_never_adjusted():
    """Back-adjustment convention: the most recent bar keeps its raw close."""
    p = _prices([10.0, 10.0, 10.0])
    divs = pd.DataFrame([{"symbol": "AAA", "ex_date": p["date"].iloc[1], "amount": 1.0}])
    out = adjust.apply_adjustments(p, dividends=divs)
    assert out["adj_close"].iloc[-1] == pytest.approx(10.0)


def test_single_dividend_scales_prior_bars_only():
    # closes 10,10,10 ; $1 dividend ex on bar 2 -> factor 0.9 applies to bar 1 only
    p = _prices([10.0, 10.0, 10.0])
    divs = pd.DataFrame([{"symbol": "AAA", "ex_date": p["date"].iloc[1], "amount": 1.0}])
    out = adjust.apply_adjustments(p, dividends=divs)
    assert out["adj_close"].iloc[0] == pytest.approx(9.0)
    assert out["adj_close"].iloc[1] == pytest.approx(10.0)
    assert out["adj_close"].iloc[2] == pytest.approx(10.0)


def test_dividend_raises_measured_total_return():
    """The whole point: raw returns understate; adjusted returns capture the payout."""
    p = _prices([100.0, 100.0])
    divs = pd.DataFrame([{"symbol": "AAA", "ex_date": p["date"].iloc[1], "amount": 2.0}])
    out = adjust.apply_adjustments(p, dividends=divs)
    tr = adjust.total_return_series(out)["ret"].iloc[-1]
    raw = 100.0 / 100.0 - 1.0
    assert raw == pytest.approx(0.0)
    assert tr > 0.019  # ~2% total return recovered


def test_two_for_one_split_halves_prior_bars():
    p = _prices([100.0, 50.0, 50.0])
    sp = pd.DataFrame([{"symbol": "AAA", "ex_date": p["date"].iloc[1], "ratio": 2.0}])
    out = adjust.apply_adjustments(p, splits=sp)
    assert out["adj_close"].iloc[0] == pytest.approx(50.0)
    assert out["adj_close"].iloc[1] == pytest.approx(50.0)
    # and the split no longer looks like a -50% return
    tr = adjust.total_return_series(out)["ret"].iloc[1]
    assert tr == pytest.approx(0.0)


def test_split_ignored_when_source_already_split_adjusted():
    """Guard against double-applying splits to Stooq-style series."""
    p = _prices([50.0, 50.0, 50.0])
    sp = pd.DataFrame([{"symbol": "AAA", "ex_date": p["date"].iloc[1], "ratio": 2.0}])
    out = adjust.apply_adjustments(p, splits=sp, splits_already_applied=True)
    assert (out["adj_close"] == out["close"]).all()


def test_multiple_symbols_do_not_bleed():
    a = _prices([10.0, 10.0], symbol="AAA")
    b = _prices([20.0, 20.0], symbol="BBB")
    p = pd.concat([a, b], ignore_index=True)
    divs = pd.DataFrame([{"symbol": "AAA", "ex_date": a["date"].iloc[1], "amount": 1.0}])
    out = adjust.apply_adjustments(p, dividends=divs)
    bbb = out[out["symbol"] == "BBB"]
    assert (bbb["adj_close"] == bbb["close"]).all()


def test_missing_columns_raise():
    with pytest.raises(ValueError):
        adjust.apply_adjustments(pd.DataFrame({"symbol": ["A"], "date": ["2023-01-01"]}))
