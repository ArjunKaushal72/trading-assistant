"""Metrics tested against hand-worked values — no metric trusted on faith."""
import numpy as np
import pandas as pd
import pytest

from ta_research.backtest import metrics as m


def test_total_return_compounds():
    assert m.total_return(pd.Series([0.1, 0.1])) == pytest.approx(0.21)


def test_total_return_empty_is_zero():
    assert m.total_return(pd.Series([], dtype=float)) == 0.0


def test_max_drawdown_peak_to_trough():
    eq = pd.Series([100.0, 110.0, 99.0, 120.0])
    assert m.max_drawdown(eq) == pytest.approx(99.0 / 110.0 - 1.0)


def test_max_drawdown_monotonic_up_is_zero():
    assert m.max_drawdown(pd.Series([100.0, 101.0, 102.0])) == pytest.approx(0.0)


def test_sharpe_zero_when_no_volatility():
    assert m.sharpe(pd.Series([0.0, 0.0, 0.0])) == 0.0


def test_sharpe_matches_manual():
    r = pd.Series([0.01, 0.02, 0.015, -0.005, 0.01])
    expected = r.mean() / r.std(ddof=1) * np.sqrt(252)
    assert m.sharpe(r, periods_per_year=252) == pytest.approx(expected)


def test_cagr_equals_total_return_over_exactly_one_year():
    r = pd.Series([0.0004] * 252)
    growth = float((1.0 + r).prod())
    assert m.cagr(r, 252) == pytest.approx(growth - 1.0, rel=1e-9)


def test_summary_has_every_bar_metric():
    out = m.summary(pd.Series([0.01, -0.01, 0.02]))
    for k in ("total_return", "cagr", "sharpe", "ann_volatility",
              "max_drawdown", "max_drawdown_pct", "calmar"):
        assert k in out
