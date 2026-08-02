"""Resumable, quota-aware universe backfill (Phase 1, Step 3).

Run it once a day until it reports COMPLETE. Each run:
  * spends at most --cap calls (default 240, under FMP free's ~250)
  * fetches adjusted EOD (primary research series) for pending symbols
  * retries transient failures (the PG-402-mid-burst case) up to 3 times
  * checkpoints every symbol so tomorrow's run resumes exactly where it stopped
  * surfaces permanently-failed symbols as REAL universe holes

Endpoints are prioritised: adjusted prices first (that is what research needs),
then a raw+events audit sample for reconciliation against FMP's own adjClose.

    uv run python scripts/backfill.py --cap 240
    uv run python scripts/backfill.py --status      # progress only, 0 calls
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from ta_research.data import budget, fmp
from ta_research.storage import duckdb_store as store

DB = "data/research.duckdb"
UNIVERSE = "config/universe_us.csv"
PROVIDER = "fmp"
EP_ADJ = "eod_adjusted"
EP_RAW = "eod_raw"
EP_DIV = "dividends"
EP_SPLIT = "splits"
AUDIT_SAMPLE = 25          # names reconciled raw-vs-adjusted
SLEEP_S = 0.9              # conservative; free tiers throttle aggressively
CONSECUTIVE_FAIL_ABORT = 8 # circuit breaker: stop before wasting the day's quota
THROTTLE_BACKOFF_S = 20


def load_universe(path: str) -> list[str]:
    df = pd.read_csv(path, comment="#")
    return [s.strip().upper() for s in df["symbol"].dropna().tolist()]


def _status_code(exc: Exception) -> int | None:
    """Status now travels as an attribute on FMPError -- no string parsing.

    The previous substring-matching version silently mislabelled every HTTP 402
    as an unclassified failure, which hid the fact that the free plan whitelists
    symbols. Structured errors are the fix.
    """
    return getattr(exc, "status_code", None)


def run_endpoint(con, endpoint: str, fetcher, allowance: int, key: str) -> tuple[int, bool]:
    """Spend up to `allowance` calls on one endpoint.

    Returns (calls_used, aborted). Aborts on a run of consecutive THROTTLE or
    server failures -- a previous run burned an entire day's quota pushing
    requests at an endpoint that was refusing all of them. Plan-restricted (402)
    responses are terminal per symbol but do NOT trip the breaker, since they are
    an expected property of the plan rather than a sign the API is unhealthy.
    """
    used = 0
    consecutive = 0
    while used < allowance:
        tasks = budget.next_tasks(con, PROVIDER, endpoint, 1)
        if not tasks:
            break
        t = tasks[0]
        try:
            df = fetcher(t.symbol, key=key)
            src = "fmp_adj" if endpoint == EP_ADJ else "fmp"
            if endpoint in (EP_ADJ, EP_RAW):
                store.upsert_prices(con, df, source=src)
            budget.mark_done(con, t)
            budget.record_call(con, PROVIDER, endpoint, t.symbol, True, "200")
            print(f"    OK   {t.symbol:8s} {endpoint:12s} {len(df):5d} rows")
            consecutive = 0
        except Exception as e:
            code = _status_code(e)
            budget.record_call(con, PROVIDER, endpoint, t.symbol, False, str(code))
            if budget.is_plan_restricted(code):
                budget.mark_restricted(con, t, f"{code}: {e}")
                print(f"    SKIP {t.symbol:8s} {endpoint:12s} {code} "
                      f"(not on plan -- terminal, excluded from universe)")
                consecutive = 0          # expected, not an API health signal
            else:
                budget.mark_failed(con, t, f"{code}: {e}")
                consecutive += 1
                kind = "transient, will retry" if budget.is_transient(code) else "unknown"
                print(f"    FAIL {t.symbol:8s} {endpoint:12s} {code} ({kind})")
                if code == 429:
                    print(f"         throttled; backing off {THROTTLE_BACKOFF_S}s")
                    time.sleep(THROTTLE_BACKOFF_S)
                if consecutive >= CONSECUTIVE_FAIL_ABORT:
                    print(f"\n  !! CIRCUIT BREAKER: {consecutive} consecutive failures.")
                    print("     Aborting to preserve the remaining daily quota.")
                    return used + 1, True
        used += 1
        time.sleep(SLEEP_S)
    return used, False


def print_status(con) -> None:
    print("\n  progress:")
    for ep, done, pending, failed, restricted, total in budget.progress(con, PROVIDER):
        pct = (done / total * 100) if total else 0
        print(f"    {ep:12s} done={done:4d} pending={pending:4d} failed={failed:3d} "
              f"restricted={restricted:4d} total={total:4d}  ({pct:.0f}%)")
    fails = budget.permanent_failures(con, PROVIDER)
    if fails:
        print(f"\n  !! {len(fails)} PERMANENT failures (real universe holes):")
        for ep, sym, att, err in fails[:20]:
            print(f"     {sym:8s} {ep:12s} attempts={att} {str(err)[:70]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=240,
                    help="max API calls this run (FMP free is ~250/day)")
    ap.add_argument("--status", action="store_true", help="show progress, spend nothing")
    ap.add_argument("--universe", default=UNIVERSE)
    ap.add_argument("--reset-failed", action="store_true",
                    help="return failed/restricted tasks to pending (use after a "
                         "client-side misclassification); spends nothing")
    ap.add_argument("--export-available", action="store_true",
                    help="write config/universe_available.csv from confirmed symbols")
    args = ap.parse_args()

    con = store.connect(DB)
    budget.init(con)

    symbols = load_universe(args.universe)
    budget.seed_tasks(con, PROVIDER, EP_ADJ, symbols)
    audit = symbols[:AUDIT_SAMPLE]
    for ep in (EP_RAW, EP_DIV, EP_SPLIT):
        budget.seed_tasks(con, PROVIDER, ep, audit)

    print("=" * 70)
    print(f"BACKFILL  universe={len(symbols)}  audit_sample={len(audit)}")
    print("=" * 70)

    if args.reset_failed:
        n = budget.reset_failures(con, PROVIDER)
        print(f"  reset complete: {n} tasks now pending (0 API calls spent)")
        print_status(con)
        con.close()
        return 0

    if args.export_available:
        avail = budget.available_symbols(con, PROVIDER, EP_ADJ)
        restricted = budget.restricted_symbols(con, PROVIDER, EP_ADJ)
        Path("config").mkdir(exist_ok=True)
        with open("config/universe_available.csv", "w") as fh:
            fh.write("# Symbols CONFIRMED fetchable on the current data plan.\n")
            fh.write(f"# Generated from fetch_state. {len(avail)} available, "
                     f"{len(restricted)} restricted.\n")
            fh.write("# A universe this size is documented, not silently assumed.\n")
            fh.write("symbol\n")
            fh.write("\n".join(avail) + "\n")
        print(f"  wrote config/universe_available.csv "
              f"({len(avail)} available / {len(restricted)} restricted)")
        con.close()
        return 0

    if args.status:
        print_status(con)
        con.close()
        return 0

    key = fmp.get_key()
    if not key:
        print("no FMP key in Credential Manager; aborting")
        return 2

    left = min(args.cap, budget.remaining_budget(con, PROVIDER, args.cap))
    print(f"  budget this run: {left} calls "
          f"(already used today: {budget.calls_used_today(con, PROVIDER)})\n")

    plan = [(EP_ADJ, fmp.fetch_prices_adjusted),
            (EP_RAW, fmp.fetch_prices),
            (EP_DIV, fmp.fetch_dividends),
            (EP_SPLIT, fmp.fetch_splits)]
    for ep, fn in plan:
        if left <= 0:
            print(f"  budget exhausted before {ep}; resumes next run")
            break
        spent, aborted = run_endpoint(con, ep, fn, left, key)
        left -= spent
        if aborted:
            break

    print_status(con)
    remaining = sum(row[2] for row in budget.progress(con, PROVIDER))
    print("\n" + ("  COMPLETE -- all endpoints backfilled."
                  if remaining == 0 else
                  f"  {remaining} tasks remain. Re-run tomorrow to continue."))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
