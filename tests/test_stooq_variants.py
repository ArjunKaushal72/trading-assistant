from ta_research.data import stooq


def test_browser_user_agent_is_sent():
    """Bare python-requests UA got HTTP 404 from Stooq in the live slice run."""
    assert "Mozilla" in stooq.HEADERS["User-Agent"]
    assert "python-requests" not in stooq.HEADERS["User-Agent"]


def test_params_omit_dates_when_requested():
    p = stooq.build_url_params("aapl.us", None, None)
    assert "d1" not in p and "d2" not in p
    assert p["s"] == "aapl.us" and p["i"] == "d"


def test_params_include_dates_when_given():
    p = stooq.build_url_params("aapl.us", "2010-01-01", "2026-07-23")
    assert p["d1"] == "20100101" and p["d2"] == "20260723"


def test_html_response_rejected_not_parsed():
    import pytest
    with pytest.raises(ValueError, match="HTML"):
        stooq.parse_stooq_csv("<!DOCTYPE html><html><body>blocked</body></html>")
