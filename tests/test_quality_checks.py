import pandas as pd

from ta_research.quality import checks


def test_clean_frame_passes_all(clean_nyse_frame):
    issues = checks.run_all(clean_nyse_frame, "NYSE")
    assert issues == [], f"clean frame should have no issues, got {issues}"


def test_duplicate_bars_detected(clean_nyse_frame):
    dupe = pd.concat([clean_nyse_frame, clean_nyse_frame.iloc[[0]]], ignore_index=True)
    issues = checks.check_no_duplicate_bars(dupe)
    assert len(issues) == 1
    assert issues[0].check == "no_duplicate_bars"
    assert issues[0].n == 2  # both members of the duplicate pair


def test_ohlc_inconsistency_detected(clean_nyse_frame):
    bad = clean_nyse_frame.copy()
    bad.loc[3, "high"] = bad.loc[3, "low"] - 1.0  # high < low: impossible
    issues = checks.check_ohlc_consistency(bad)
    assert len(issues) == 1 and issues[0].n == 1


def test_negative_volume_detected(clean_nyse_frame):
    bad = clean_nyse_frame.copy()
    bad.loc[2, "volume"] = -5
    issues = checks.check_ohlc_consistency(bad)
    assert len(issues) == 1 and issues[0].n == 1


def test_internal_calendar_gap_detected(clean_nyse_frame):
    # Drop a mid-range session -> must be flagged
    gapped = clean_nyse_frame.drop(index=5).reset_index(drop=True)
    issues = checks.check_calendar_gaps(gapped, "NYSE")
    assert len(issues) == 1 and issues[0].n == 1


def test_late_listing_not_flagged_as_gap(clean_nyse_frame):
    # Removing the FIRST rows shifts the symbol's start; no internal gap remains
    trimmed = clean_nyse_frame.iloc[3:].reset_index(drop=True)
    issues = checks.check_calendar_gaps(trimmed, "NYSE")
    assert issues == []
