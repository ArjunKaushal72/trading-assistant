"""Parser tests use recorded fixture strings, never the live network."""
import json

import pandas as pd
import pytest

from ta_research.data import boc, fmp, stooq

STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2010-01-04,7.62,7.66,7.58,7.64,123456\n"
    "2010-01-05,7.66,7.70,7.62,7.65,111111\n"
)


def test_stooq_parses_clean_csv():
    df = stooq.parse_stooq_csv(STOOQ_CSV)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["close"].iloc[0] == 7.64


@pytest.mark.parametrize("body", ["", "No data", "   "])
def test_stooq_rejects_empty(body):
    with pytest.raises(ValueError):
        stooq.parse_stooq_csv(body)


def test_stooq_symbol_mapping():
    assert stooq.stooq_symbol("AAPL", "US") == "aapl.us"
    assert stooq.stooq_symbol("RY.TO", "CA") == "ry.ca"


# NOTE: source_diag confirmed the /full endpoint returns NO adjusted close.
# Adjusted prices come from historical-price-eod/dividend-adjusted instead.
FMP_JSON = json.dumps([
    {"date": "2010-01-04", "open": 7.62, "high": 7.66, "low": 7.58,
     "close": 7.64, "volume": 123456, "vwap": 7.62},
    {"date": "2010-01-05", "open": 7.66, "high": 7.70, "low": 7.62,
     "close": 7.65, "volume": 111111, "vwap": 7.66},
])


def test_fmp_parses_raw_list_without_adjclose():
    df = fmp.parse_stable_eod(json.loads(FMP_JSON))
    assert len(df) == 2
    assert df["close"].iloc[0] == 7.64
    assert df["adj_close"].isna().all()


def test_fmp_handles_dict_wrapper():
    wrapped = {"historical": json.loads(FMP_JSON)}
    df = fmp.parse_stable_eod(wrapped)
    assert len(df) == 2


def test_fmp_empty_raises():
    with pytest.raises(ValueError):
        fmp.parse_stable_eod([])


BOC_JSON = {
    "observations": [
        {"d": "2010-01-04", "FXUSDCAD": {"v": "1.0466"}},
        {"d": "2010-01-05", "FXUSDCAD": {"v": "1.0410"}},
        {"d": "2010-01-06", "FXUSDCAD": {"v": ""}},  # missing value -> skipped
    ]
}


def test_boc_parses_and_skips_blanks():
    df = boc.parse_valet(BOC_JSON, "FXUSDCAD")
    assert len(df) == 2  # blank skipped
    assert abs(df["rate"].iloc[0] - 1.0466) < 1e-9
