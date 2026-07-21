import pandas as pd
import pytest

from ta_research.data.calendars import valid_sessions


@pytest.fixture
def clean_nyse_frame() -> pd.DataFrame:
    """A small, internally-consistent, gap-free NYSE panel for one symbol."""
    sessions = valid_sessions("NYSE", "2023-01-03", "2023-01-31")
    n = len(sessions)
    base = 100.0
    rows = []
    for i, d in enumerate(sessions):
        c = base + i
        rows.append(dict(symbol="AAA", date=d,
                         open=c - 0.5, high=c + 1.0, low=c - 1.0,
                         close=c, volume=1_000_000 + i))
    return pd.DataFrame(rows)
