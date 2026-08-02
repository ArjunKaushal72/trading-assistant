import duckdb
import pytest

from ta_research.data import budget


@pytest.fixture
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "b.duckdb"))
    budget.init(c)
    return c


def test_budget_starts_full(con):
    assert budget.remaining_budget(con, "fmp", 250) == 250


def test_calls_decrement_budget(con):
    for i in range(5):
        budget.record_call(con, "fmp", "eod", f"S{i}", True, "200")
    assert budget.calls_used_today(con, "fmp") == 5
    assert budget.remaining_budget(con, "fmp", 250) == 245


def test_budget_floors_at_zero(con):
    for i in range(12):
        budget.record_call(con, "fmp", "eod", f"S{i}", True, "200")
    assert budget.remaining_budget(con, "fmp", 10) == 0


def test_next_tasks_respects_limit_and_budget(con):
    budget.seed_tasks(con, "fmp", "eod", [f"S{i}" for i in range(10)])
    assert len(budget.next_tasks(con, "fmp", "eod", 3)) == 3
    assert budget.next_tasks(con, "fmp", "eod", 0) == []


def test_seed_is_idempotent(con):
    budget.seed_tasks(con, "fmp", "eod", ["AAPL", "MSFT"])
    budget.seed_tasks(con, "fmp", "eod", ["AAPL", "MSFT"])
    total = con.execute("SELECT count(*) FROM fetch_state").fetchone()[0]
    assert total == 2


def test_resume_skips_completed(con):
    """The core resumability property: a second run must not redo done work."""
    budget.seed_tasks(con, "fmp", "eod", ["A", "B", "C"])
    first = budget.next_tasks(con, "fmp", "eod", 2)
    for t in first:
        budget.mark_done(con, t)
    second = budget.next_tasks(con, "fmp", "eod", 10)
    assert {t.symbol for t in second} == {"C"}


def test_failed_task_is_retried_until_max_attempts(con):
    budget.seed_tasks(con, "fmp", "eod", ["PG"])
    for _ in range(budget.MAX_ATTEMPTS):
        tasks = budget.next_tasks(con, "fmp", "eod", 1)
        assert len(tasks) == 1, "should still be retryable"
        budget.mark_failed(con, tasks[0], "HTTP 402 transient")
    assert budget.next_tasks(con, "fmp", "eod", 1) == []


def test_permanent_failures_are_surfaced_not_hidden(con):
    budget.seed_tasks(con, "fmp", "eod", ["PG"])
    for _ in range(budget.MAX_ATTEMPTS):
        t = budget.next_tasks(con, "fmp", "eod", 1)[0]
        budget.mark_failed(con, t, "HTTP 402")
    fails = budget.permanent_failures(con, "fmp")
    assert len(fails) == 1 and fails[0][1] == "PG"


def test_transient_classification():
    """402 was RECLASSIFIED after the 2026-07-23 backfill: it is a plan
    restriction, not a hiccup. See test_402_is_plan_restricted_not_transient."""
    assert budget.is_transient(429) is True
    assert budget.is_transient(503) is True
    assert budget.is_transient(404) is False
    assert budget.is_transient(402) is False


def test_progress_counts(con):
    budget.seed_tasks(con, "fmp", "eod", ["A", "B", "C"])
    budget.mark_done(con, budget.Task("fmp", "eod", "A"))
    budget.mark_failed(con, budget.Task("fmp", "eod", "B"), "err")
    rows = {r[0]: r for r in budget.progress(con, "fmp")}
    ep, done, pending, failed, restricted, total = rows["eod"]
    assert done == 1 and pending == 1 and failed == 1 and total == 3


# --- incident regression tests: FMP free-tier symbol whitelist (2026-07-23) ---

def test_402_is_plan_restricted_not_transient():
    """HTTP 402 means 'not on your plan'. Retrying burns quota and, worse,
    would drive real symbols to permanent-failure and shrink the universe."""
    assert budget.is_plan_restricted(402) is True
    assert budget.is_transient(402) is False
    assert budget.is_transient(429) is True
    assert budget.is_plan_restricted(429) is False


def test_restricted_task_is_never_requeued(con):
    budget.seed_tasks(con, "fmp", "eod", ["PG", "KO"])
    budget.mark_restricted(con, budget.Task("fmp", "eod", "PG"), "402 not on plan")
    queued = {t.symbol for t in budget.next_tasks(con, "fmp", "eod", 10)}
    assert queued == {"KO"}, "restricted symbols must not consume further quota"


def test_available_and_restricted_are_both_reported(con):
    budget.seed_tasks(con, "fmp", "eod", ["AAPL", "PG"])
    budget.mark_done(con, budget.Task("fmp", "eod", "AAPL"))
    budget.mark_restricted(con, budget.Task("fmp", "eod", "PG"), "402")
    assert budget.available_symbols(con, "fmp", "eod") == ["AAPL"]
    assert budget.restricted_symbols(con, "fmp", "eod") == ["PG"]


def test_reset_failures_recovers_misclassified_symbols(con):
    """A client-side misclassification must never permanently delete a symbol."""
    budget.seed_tasks(con, "fmp", "eod", ["A", "B", "C"])
    budget.mark_failed(con, budget.Task("fmp", "eod", "A"), "wrongly failed")
    budget.mark_restricted(con, budget.Task("fmp", "eod", "B"), "wrongly restricted")
    budget.reset_failures(con, "fmp")
    queued = {t.symbol for t in budget.next_tasks(con, "fmp", "eod", 10)}
    assert queued == {"A", "B", "C"}
    rows = con.execute("SELECT attempts FROM fetch_state").fetchall()
    assert all(r[0] == 0 for r in rows), "attempt counters must be zeroed on reset"


def test_progress_reports_restricted_column(con):
    budget.seed_tasks(con, "fmp", "eod", ["A", "B"])
    budget.mark_restricted(con, budget.Task("fmp", "eod", "A"), "402")
    ep, done, pending, failed, restricted, total = budget.progress(con, "fmp")[0]
    assert restricted == 1 and pending == 1 and total == 2


# --- rolling-window limits (Tiingo = 50/hour, not 50/day) --------------------

def test_daily_counter_does_not_govern_hourly_provider(con):
    """The wedge bug: 53 calls today + a 45 cap made the DAILY counter report
    0 available for the rest of the UTC day, even though the rolling hour had
    already freed capacity."""
    from datetime import timedelta
    old = budget._now() - timedelta(hours=3)
    # call_day is today (the daily counter keys on it); the timestamp is 3h ago so
    # the rolling hour has already freed the capacity. Dating the rows by old.date()
    # made this fail whenever it ran in the first 3h of the UTC day.
    for i in range(53):
        con.execute("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?)",
                    [old, budget._now().date(), "tiingo", "daily", f"S{i}", True, "200"])
    assert budget.remaining_budget(con, "tiingo", 45) == 0      # old behaviour
    assert budget.remaining_in_window(con, "tiingo", 50, 60) == 50  # correct


def test_window_counts_only_recent_calls(con):
    from datetime import timedelta
    now = budget._now()
    for i in range(10):
        ts = now - timedelta(minutes=5)
        con.execute("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?)",
                    [ts, ts.date(), "tiingo", "daily", f"R{i}", True, "200"])
    for i in range(20):
        ts = now - timedelta(minutes=120)
        con.execute("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?)",
                    [ts, ts.date(), "tiingo", "daily", f"O{i}", True, "200"])
    assert budget.calls_used_in_window(con, "tiingo", 60) == 10
    assert budget.remaining_in_window(con, "tiingo", 50, 60) == 40


def test_seconds_until_slot_free_zero_when_capacity_exists(con):
    assert budget.seconds_until_slot_free(con, "tiingo", 50, 60) == 0


def test_seconds_until_slot_free_is_partial_not_full_hour(con):
    """Sleeping a flat hour wastes time when the window is partially aged."""
    from datetime import timedelta
    now = budget._now()
    for i in range(50):
        ts = now - timedelta(minutes=45)      # 45 min old
        con.execute("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?)",
                    [ts, ts.date(), "tiingo", "daily", f"S{i}", True, "200"])
    wait = budget.seconds_until_slot_free(con, "tiingo", 50, 60)
    assert 0 < wait < 20 * 60, f"expected ~15min, got {wait}s"


def test_requeue_does_not_penalise_attempts(con):
    """A provider rate cap is not the symbol's fault."""
    budget.seed_tasks(con, "tiingo", "daily", ["ED"])
    t = budget.Task("tiingo", "daily", "ED")
    budget.requeue(con, t)
    attempts = con.execute(
        "SELECT attempts FROM fetch_state WHERE symbol='ED'").fetchone()[0]
    assert attempts == 0
    assert [x.symbol for x in budget.next_tasks(con, "tiingo", "daily", 5)] == ["ED"]
