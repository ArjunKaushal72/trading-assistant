"""Regression tests for the status-code-as-data fix.

Incident: 402 responses were raised as PermissionError('fmp: premium-only ...'),
and the caller recovered the code by substring-searching the message. "402"
never appeared there, so 173 plan-restricted symbols were logged as
unclassified 'hard' failures and the real cause stayed hidden for a full run.
"""
import pytest

from ta_research.data import fmp


def test_error_carries_status_as_attribute():
    e = fmp.FMPError(402, "Premium Query Parameter", body="upgrade your plan")
    assert e.status_code == 402
    assert "402" in str(e)


def test_status_recoverable_without_string_parsing():
    e = fmp.FMPError(429, "rate limited")
    assert getattr(e, "status_code", None) == 429


def test_network_error_has_none_status():
    e = fmp.FMPError(None, "network error")
    assert e.status_code is None


def test_error_message_no_longer_hides_the_code():
    """The exact regression: a 402 whose message omits the number."""
    e = fmp.FMPError(402, "premium-only for these params")
    assert "402" in str(e), "status must be visible in the message too"
