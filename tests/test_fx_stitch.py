import pandas as pd

from ta_research.data import boc


def _fx(dates, rates):
    return pd.DataFrame({"date": pd.to_datetime(dates).date, "rate": rates})


def test_current_series_wins_on_overlap():
    legacy = _fx(["2016-12-30", "2017-01-03"], [1.30, 1.31])
    current = _fx(["2017-01-03", "2017-01-04"], [1.35, 1.36])
    out = boc.stitch_fx(legacy, current)
    row = out[out["date"] == pd.to_datetime("2017-01-03").date()]
    assert len(row) == 1
    assert row["rate"].iloc[0] == 1.35
    assert row["series_used"].iloc[0] == "current"


def test_legacy_fills_pre_2017_gap():
    legacy = _fx(["2010-01-04"], [1.04])
    current = _fx(["2017-01-03"], [1.35])
    out = boc.stitch_fx(legacy, current)
    assert len(out) == 2
    assert out["date"].min() == pd.to_datetime("2010-01-04").date()
    assert out.iloc[0]["series_used"] == "legacy"


def test_no_legacy_returns_current_only():
    current = _fx(["2017-01-03"], [1.35])
    out = boc.stitch_fx(pd.DataFrame(), current)
    assert len(out) == 1 and out["series_used"].iloc[0] == "current"


def test_output_sorted_and_unique():
    legacy = _fx(["2015-01-02", "2013-01-02"], [1.2, 1.1])
    current = _fx(["2017-01-03"], [1.35])
    out = boc.stitch_fx(legacy, current)
    assert list(out["date"]) == sorted(out["date"])
    assert out["date"].is_unique


def test_overlap_validation_flags_large_divergence():
    legacy = _fx(["2017-01-03", "2017-01-04"], [1.30, 1.31])
    current = _fx(["2017-01-03", "2017-01-04"], [1.305, 1.50])  # 2nd is way off
    out = boc.validate_overlap(legacy, current, tolerance=0.01)
    assert len(out) == 2
    assert bool(out["within_tolerance"].iloc[0]) is True
    assert bool(out["within_tolerance"].iloc[1]) is False


def test_overlap_empty_when_no_shared_dates():
    legacy = _fx(["2010-01-04"], [1.04])
    current = _fx(["2017-01-03"], [1.35])
    assert boc.validate_overlap(legacy, current).empty
