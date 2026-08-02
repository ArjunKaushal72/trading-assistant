"""Backtest engine and performance metrics.

Public surface:
    run_backtest, CostModel, BacktestResult   — the engine
    metrics                                    — the metrics module
"""
from __future__ import annotations

from ta_research.backtest import metrics
from ta_research.backtest.engine import BacktestResult, CostModel, run_backtest

__all__ = ["run_backtest", "CostModel", "BacktestResult", "metrics"]
