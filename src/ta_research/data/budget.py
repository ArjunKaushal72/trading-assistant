"""API call budgeting and resumable checkpointing.

FMP free exposes NO rate-limit headers (source_diag confirmed), so the client
must track its own spend. The daily cap is ~250 calls and there are no batch
endpoints, so a universe backfill is inherently multi-day: this module decides
what to fetch next, stops before the cap, and lets the next run resume exactly
where the last one stopped.

Design note on WHY multi-day is fine: Phase 1 research backtests history, so the
backfill is a ONE-TIME cost. A daily whole-universe refresh only becomes
necessary at live human-in-the-loop operation (Phase 8) -- which is precisely
the point where paying for a higher tier is justified by evidence rather than
assumed up front.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import duckdb

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    call_ts   TIMESTAMP NOT NULL,
    call_day  DATE      NOT NULL,
    provider  VARCHAR   NOT NULL,
    endpoint  VARCHAR   NOT NULL,
    symbol    VARCHAR,
    ok        BOOLEAN   NOT NULL,
    status    VARCHAR
);

CREATE TABLE IF NOT EXISTS fetch_state (
    provider   VARCHAR NOT NULL,
    endpoint   VARCHAR NOT NULL,
    symbol     VARCHAR NOT NULL,
    status     VARCHAR NOT NULL,   -- pending | done | failed
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error VARCHAR,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (provider, endpoint, symbol)
);
"""

# 402 is NOT transient. Empirically (backfill run 2026-07-23) FMP's free tier
# whitelists symbols: 41 of 214 returned data, 173 returned 402 consistently on
# repeat. Retrying a plan restriction burns quota and, worse, would drive those
# symbols to "permanent failure" and silently shrink the universe to the
# whitelist without anyone noticing.
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
PLAN_RESTRICTED_STATUSES = {401, 402, 403}
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Task:
    provider: str
    endpoint: str
    symbol: str


def init(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(BUDGET_SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today() -> date:
    return _now().date()


def calls_used_today(con, provider: str) -> int:
    row = con.execute(
        "SELECT count(*) FROM api_calls WHERE provider=? AND call_day=?",
        [provider, _today()],
    ).fetchone()
    return int(row[0]) if row else 0


def remaining_budget(con, provider: str, daily_cap: int) -> int:
    """Remaining calls against a DAILY cap (FMP-style)."""
    return max(0, daily_cap - calls_used_today(con, provider))


# --- rolling-window limits -------------------------------------------------
# Tiingo's free tier caps requests per ROLLING HOUR, not per day. Applying the
# daily counter to an hourly cap wedges the backfill at "0 calls available" for
# the rest of the UTC day even though capacity has already freed up. Providers
# therefore declare which window they use.

def calls_used_in_window(con, provider: str, window_minutes: int = 60) -> int:
    row = con.execute("""
        SELECT count(*) FROM api_calls
        WHERE provider=? AND call_ts >= (? - INTERVAL 1 MINUTE * ?);
    """, [provider, _now(), window_minutes]).fetchone()
    return int(row[0]) if row else 0


def remaining_in_window(con, provider: str, cap: int,
                        window_minutes: int = 60) -> int:
    return max(0, cap - calls_used_in_window(con, provider, window_minutes))


def seconds_until_slot_free(con, provider: str, cap: int,
                            window_minutes: int = 60) -> int:
    """Seconds until at least one call slot leaves the rolling window.

    Sleeping a flat hour wastes time whenever the window is partially aged; this
    computes the real wait from the oldest in-window call.
    """
    if remaining_in_window(con, provider, cap, window_minutes) > 0:
        return 0
    row = con.execute("""
        SELECT min(call_ts) FROM api_calls
        WHERE provider=? AND call_ts >= (? - INTERVAL 1 MINUTE * ?);
    """, [provider, _now(), window_minutes]).fetchone()
    if not row or row[0] is None:
        return 0
    oldest = row[0]
    elapsed = (_now() - oldest).total_seconds()
    return max(0, int(window_minutes * 60 - elapsed) + 5)   # +5s safety


def requeue(con, task: "Task") -> None:
    """Return a task to pending WITHOUT an attempt penalty.

    A provider-side rate cap is not the symbol's fault; charging it an attempt
    would eventually mark a perfectly good symbol as permanently failed.
    """
    con.execute("""
        UPDATE fetch_state SET status='pending', last_error=NULL, updated_at=?
        WHERE provider=? AND endpoint=? AND symbol=?;
    """, [_now(), task.provider, task.endpoint, task.symbol])


def record_call(con, provider: str, endpoint: str, symbol: str | None,
                ok: bool, status: str = "") -> None:
    con.execute("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?)",
                [_now(), _today(), provider, endpoint, symbol, ok, status])


def seed_tasks(con, provider: str, endpoint: str, symbols: list[str]) -> int:
    """Register symbols as pending for an endpoint. Idempotent."""
    n = 0
    for s in symbols:
        con.execute("""
            INSERT INTO fetch_state VALUES (?,?,?,'pending',0,NULL,?)
            ON CONFLICT (provider, endpoint, symbol) DO NOTHING;
        """, [provider, endpoint, s, _now()])
        n += 1
    return n


def next_tasks(con, provider: str, endpoint: str, limit: int) -> list[Task]:
    """Pending (or retryable) tasks, oldest first, capped at `limit`."""
    if limit <= 0:
        return []
    rows = con.execute("""
        SELECT symbol FROM fetch_state
        WHERE provider=? AND endpoint=?
          AND (status='pending' OR (status='failed' AND attempts < ?))
        ORDER BY attempts ASC, symbol ASC
        LIMIT ?;
    """, [provider, endpoint, MAX_ATTEMPTS, limit]).fetchall()
    return [Task(provider, endpoint, r[0]) for r in rows]


def mark_done(con, task: Task) -> None:
    con.execute("""
        UPDATE fetch_state SET status='done', last_error=NULL, updated_at=?
        WHERE provider=? AND endpoint=? AND symbol=?;
    """, [_now(), task.provider, task.endpoint, task.symbol])


def mark_failed(con, task: Task, error: str) -> None:
    con.execute("""
        UPDATE fetch_state
        SET status='failed', attempts=attempts+1, last_error=?, updated_at=?
        WHERE provider=? AND endpoint=? AND symbol=?;
    """, [error[:300], _now(), task.provider, task.endpoint, task.symbol])


def is_transient(status_code: int | None) -> bool:
    """Throttles and server errors are worth retrying; plan limits are not."""
    return status_code in TRANSIENT_STATUSES


def is_plan_restricted(status_code: int | None) -> bool:
    """402/401/403 == this symbol is not on the current subscription.
    Retrying cannot fix it, so it is recorded once and excluded, loudly."""
    return status_code in PLAN_RESTRICTED_STATUSES


def mark_restricted(con, task: "Task", error: str) -> None:
    """Terminal, non-retryable: symbol unavailable on this plan."""
    con.execute("""
        UPDATE fetch_state
        SET status='restricted', attempts=attempts+1, last_error=?, updated_at=?
        WHERE provider=? AND endpoint=? AND symbol=?;
    """, [error[:300], _now(), task.provider, task.endpoint, task.symbol])


def reset_failures(con, provider: str, only_status: str | None = None) -> int:
    """Return failed/restricted tasks to pending and zero their attempts.

    Needed after a misclassification incident: symbols wrongly penalised must be
    re-openable, otherwise a client-side bug permanently deletes universe members.
    """
    if only_status:
        con.execute("""UPDATE fetch_state SET status='pending', attempts=0,
                       last_error=NULL, updated_at=? WHERE provider=? AND status=?;""",
                    [_now(), provider, only_status])
    else:
        con.execute("""UPDATE fetch_state SET status='pending', attempts=0,
                       last_error=NULL, updated_at=?
                       WHERE provider=? AND status IN ('failed','restricted');""",
                    [_now(), provider])
    return con.execute("SELECT count(*) FROM fetch_state WHERE provider=? AND status='pending'",
                       [provider]).fetchone()[0]


def available_symbols(con, provider: str, endpoint: str) -> list[str]:
    """Symbols confirmed fetchable on the current plan."""
    rows = con.execute("""SELECT symbol FROM fetch_state
                          WHERE provider=? AND endpoint=? AND status='done'
                          ORDER BY symbol;""", [provider, endpoint]).fetchall()
    return [r[0] for r in rows]


def restricted_symbols(con, provider: str, endpoint: str) -> list[str]:
    rows = con.execute("""SELECT symbol FROM fetch_state
                          WHERE provider=? AND endpoint=? AND status='restricted'
                          ORDER BY symbol;""", [provider, endpoint]).fetchall()
    return [r[0] for r in rows]


def progress(con, provider: str) -> list[tuple]:
    return con.execute("""
        SELECT endpoint,
               count(*) FILTER (WHERE status='done')       AS done,
               count(*) FILTER (WHERE status='pending')    AS pending,
               count(*) FILTER (WHERE status='failed')     AS failed,
               count(*) FILTER (WHERE status='restricted') AS restricted,
               count(*)                                    AS total
        FROM fetch_state WHERE provider=? GROUP BY endpoint ORDER BY endpoint;
    """, [provider]).fetchall()


def permanent_failures(con, provider: str) -> list[tuple]:
    """Symbols that exhausted retries -- these are REAL holes in the universe
    and must be surfaced, never silently dropped."""
    return con.execute("""
        SELECT endpoint, symbol, attempts, last_error FROM fetch_state
        WHERE provider=? AND status='failed' AND attempts >= ?
        ORDER BY endpoint, symbol;
    """, [provider, MAX_ATTEMPTS]).fetchall()
