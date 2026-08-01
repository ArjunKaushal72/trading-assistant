"""Fixture tests using the EXACT payload shapes source_diag observed live."""
import pandas as pd
import pytest

from ta_research.data import fmp

# shapes copied from the live diagnostic output
RAW_EOD = [{"symbol": "AAPL", "date": "2024-01-10", "open": 184.35, "high": 186.4,
            "low": 183.92, "close": 186.19, "volume": 46792908,
            "change": 1.84, "changePercent": 0.9981, "vwap": 185.215}]

ADJ_EOD = [{"symbol": "AAPL", "date": "2026-07-23", "adjOpen": 321.73,
            "adjHigh": 323.3, "adjLow": 319.35, "adjClose": 320.4,
            "volume": 40000000}]

SPLITS = [{"symbol": "AAPL", "date": "2020-08-31", "numerator": 4,
           "denominator": 1, "splitType": "stock-split"},
          {"symbol": "AAPL", "date": "2014-06-09", "numerator": 7,
           "denominator": 1, "splitType": "stock-split"}]

DIVIDENDS = [{"symbol": "AAPL", "date": "2026-05-11", "recordDate": "2026-05-11",
              "paymentDate": "2026-05-14", "declarationDate": "2026-04-30",
              "dividend": 0.26, "adjDividend": 0.26}]


def test_raw_eod_has_no_adjusted_close():
    df = fmp.parse_stable_eod(RAW_EOD)
    assert df["close"].iloc[0] == 186.19
    assert df["adj_close"].isna().all(), "full endpoint must not fake an adj_close"


def test_adjusted_eod_parsed():
    df = fmp.parse_stable_eod_adjusted(ADJ_EOD)
    assert df["adj_close"].iloc[0] == 320.4
    assert df["close"].iloc[0] == 320.4


def test_adjusted_missing_key_raises():
    with pytest.raises(ValueError, match="adjClose"):
        fmp.parse_stable_eod_adjusted(RAW_EOD)


def test_splits_numerator_denominator_to_ratio():
    df = fmp.parse_splits(SPLITS)
    assert df["ratio"].iloc[0] == pytest.approx(4.0)   # 4-for-1
    assert df["ratio"].iloc[1] == pytest.approx(7.0)   # 7-for-1


def test_dividends_amount_and_declaration_date():
    df = fmp.parse_dividends(DIVIDENDS)
    assert df["amount"].iloc[0] == pytest.approx(0.26)
    assert "declaration_date" in df.columns
    assert df["ex_date"].iloc[0] == pd.to_datetime("2026-05-11").date()


def test_empty_payloads_raise():
    for fn in (fmp.parse_stable_eod, fmp.parse_dividends, fmp.parse_splits):
        with pytest.raises(ValueError):
            fn([])


def test_split_ratio_end_to_end_with_adjust():
    """The AAPL 4-for-1 must flatten a -75% artifact to a 0% return."""
    from ta_research.data import adjust
    dates = pd.bdate_range("2020-08-27", periods=3)
    prices = pd.DataFrame({"symbol": "AAPL", "date": dates,
                           "close": [400.0, 100.0, 100.0]})
    splits = fmp.parse_splits([SPLITS[0]]).assign(ex_date=dates[1].date())
    out = adjust.apply_adjustments(prices, splits=splits)
    assert out["adj_close"].iloc[0] == pytest.approx(100.0)
    ret = adjust.total_return_series(out)["ret"].iloc[1]
    assert ret == pytest.approx(0.0)
