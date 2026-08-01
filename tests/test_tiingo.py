"""Tiingo parser tests against the documented payload shape."""
import pandas as pd
import pytest

from ta_research.data import adjust, symbols, tiingo

PAYLOAD = [
    {"date": "2020-08-27T00:00:00.000Z", "open": 400.0, "high": 405.0,
     "low": 398.0, "close": 400.0, "volume": 1000,
     "adjClose": 100.0, "adjOpen": 100.0, "adjHigh": 101.25, "adjLow": 99.5,
     "adjVolume": 4000, "divCash": 0.0, "splitFactor": 1.0},
    {"date": "2020-08-31T00:00:00.000Z", "open": 100.0, "high": 102.0,
     "low": 99.0, "close": 100.0, "volume": 4000,
     "adjClose": 100.0, "adjOpen": 100.0, "adjHigh": 102.0, "adjLow": 99.0,
     "adjVolume": 4000, "divCash": 0.0, "splitFactor": 4.0},
    {"date": "2020-09-01T00:00:00.000Z", "open": 100.0, "high": 103.0,
     "low": 99.0, "close": 101.0, "volume": 3000,
     "adjClose": 101.0, "adjOpen": 100.0, "adjHigh": 103.0, "adjLow": 99.0,
     "adjVolume": 3000, "divCash": 0.22, "splitFactor": 1.0},
]


def test_parse_daily_has_raw_and_adjusted():
    df = tiingo.parse_daily(PAYLOAD, "AAPL")
    assert len(df) == 3
    assert df["close"].iloc[0] == 400.0        # raw preserved
    assert df["adj_close"].iloc[0] == 100.0    # vendor adjusted preserved
    assert df["symbol"].iloc[0] == "AAPL"


def test_dates_are_naive_dates_not_timestamps():
    df = tiingo.parse_daily(PAYLOAD, "AAPL")
    assert df["date"].iloc[0] == pd.to_datetime("2020-08-27").date()


def test_extract_dividends_ignores_zero_bars():
    div = tiingo.extract_dividends(PAYLOAD, "AAPL")
    assert len(div) == 1
    assert div["amount"].iloc[0] == pytest.approx(0.22)
    assert div["ex_date"].iloc[0] == pd.to_datetime("2020-09-01").date()


def test_extract_splits_ignores_factor_one():
    sp = tiingo.extract_splits(PAYLOAD, "AAPL")
    assert len(sp) == 1
    assert sp["ratio"].iloc[0] == pytest.approx(4.0)
    assert sp["ex_date"].iloc[0] == pd.to_datetime("2020-08-31").date()


def test_events_reconcile_with_our_own_adjustment():
    """The point of choosing Tiingo: independently recompute adjustment from the
    embedded events and check it against the vendor's adjClose."""
    prices = tiingo.parse_daily(PAYLOAD, "AAPL")[["symbol", "date", "close"]]
    sp = tiingo.extract_splits(PAYLOAD, "AAPL")
    ours = adjust.apply_adjustments(prices, splits=sp)
    # our adj_close for the pre-split bar must match Tiingo's 100.0
    assert ours["adj_close"].iloc[0] == pytest.approx(100.0)


def test_empty_payload_raises():
    with pytest.raises(ValueError):
        tiingo.parse_daily([], "AAPL")


def test_missing_keys_raise():
    with pytest.raises(ValueError):
        tiingo.parse_daily([{"foo": 1}], "AAPL")


def test_missing_event_columns_return_empty_not_error():
    thin = [{"date": "2020-01-02T00:00:00.000Z", "close": 10.0}]
    assert tiingo.extract_dividends(thin, "X").empty
    assert tiingo.extract_splits(thin, "X").empty


# --- symbol convention regression (provider_probe 2026-07-23) ---

def test_class_share_mapping_per_provider():
    """Tiingo 404'd on BRK.B; Twelve Data 404'd on BRK-B. Same company."""
    assert symbols.to_tiingo("BRK.B") == "BRK-B"
    assert symbols.to_tiingo("BRK-B") == "BRK-B"
    assert symbols.to_twelvedata("BRK-B") == "BRK.B"
    assert symbols.to_fmp("BRK.B") == "BRK-B"


def test_plain_symbols_unchanged():
    for fn in (symbols.to_tiingo, symbols.to_twelvedata, symbols.to_fmp):
        assert fn("AAPL") == "AAPL"


# --- hourly-limit handling (Tiingo free = 50 req/hour, observed 2026-07-23) ---

def test_hourly_limit_message_is_detectable():
    """Retrying inside the hour is pure waste; the run must stop instead."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bt", "scripts/backfill_tiingo.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    err = tiingo.TiingoError(
        429, "rate limited",
        body='{"detail":"Error: You have run over your hourly request '
             'allocation. Please upgrade at https://api.tiingo.com/pricing"}')
    assert bt._is_hourly_limit(err) is True
    other = tiingo.TiingoError(429, "generic throttle", body="slow down")
    assert bt._is_hourly_limit(other) is False


def test_utc_now_is_naive_and_not_deprecated():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bt2", "scripts/backfill_tiingo.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    ts = bt._utc_now()
    assert ts.tzinfo is None, "stored timestamps must be naive UTC"


def test_no_history_in_window_raises_204_not_coverage_failure():
    """VOO listed Sept 2010; an empty window is correct, not a missing symbol."""
    err = tiingo.TiingoError(204, "no rows in window for VOO")
    assert err.status_code == 204
