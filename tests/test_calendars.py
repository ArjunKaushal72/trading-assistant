from ta_research.data.calendars import missing_sessions, valid_sessions


def test_tsx_and_nyse_differ_on_canada_day():
    # July 3 2023: TSX closed (Canada Day observed), NYSE open.
    tsx = valid_sessions("TSX", "2023-07-03", "2023-07-03")
    nyse = valid_sessions("NYSE", "2023-07-03", "2023-07-03")
    assert len(tsx) == 0
    assert len(nyse) == 1


def test_nyse_and_tsx_differ_on_thanksgiving():
    # US Thanksgiving 2023-11-23: NYSE closed, TSX open.
    tsx = valid_sessions("TSX", "2023-11-23", "2023-11-23")
    nyse = valid_sessions("NYSE", "2023-11-23", "2023-11-23")
    assert len(tsx) == 1
    assert len(nyse) == 0


def test_missing_sessions_none_when_complete():
    sess = valid_sessions("NYSE", "2023-02-01", "2023-02-10")
    assert len(missing_sessions(sess, "NYSE", "2023-02-01", "2023-02-10")) == 0
