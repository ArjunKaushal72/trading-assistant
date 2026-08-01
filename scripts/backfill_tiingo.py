"""Resumable Tiingo backfill -- the Phase 1 acquisition run.

One call per symbol yields raw prices, adjusted prices, dividends and splits
together, so a full universe costs ~214 calls instead of ~856. Tiingo's free
tier throttles by the hour, so this stays resumable: run it, let it stop, run it
again. Nothing is lost and nothing is re-fetched.

    uv run python scripts/backfill_tiingo.py            # run until cap/throttle
    uv run python scripts/backfill_tiingo.py --status   # progress, 0 calls
    uv run python scripts/backfill_tiingo.py --cap 40   # smaller bite

Every symbol's own adjustment is recomputed from its embedded events and
compared to Tiingo's adjClose; divergences are recorded, not swallowed.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import pandas as pd

from ta_research.data import adjust, budget, tiingo
from ta_research.storage import duckdb_store as store

DB = "data/research.duckdb"
UNIVERSE = "config/universe_us.csv"
PROVIDER = "tiingo"
ENDPOINT = "daily"
SLEEP_S = 1.5                 # free tier throttles hourly; stay polite
CONSECUTIVE_FAIL_ABORT = 6
THROTTLE_BACKOFF_S = 30
RECON_TOLERANCE = 0.005       # 0.5% divergence vs vendor adjClose
HOURLY_LIMIT_MARKERS = ("hourly request allocation", "hourly limit")
HOURLY_CAP = 50               # Tiingo free: 50 requests per ROLLING HOUR
WINDOW_MIN = 60


def _utc_now():
    """Naive UTC timestamp. pd.Timestamp.utcnow() is deprecated in pandas 4."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_hourly_limit(exc: Exception) -> bool:
    """Tiingo free allows 50 req/hour. Retrying inside that window is pure
    waste, so an hourly-cap 429 stops the run instead of burning retries."""
    body = (getattr(exc, "body", "") or "") + str(exc)
    return any(m in body.lower() for m in HOURLY_LIMIT_MARKERS)

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol VARCHAR NOT NULL, ex_date DATE NOT NULL, kind VARCHAR NOT NULL,
    value DOUBLE, source VARCHAR NOT NULL, ingested_at TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, ex_date, kind, source)
);
CREATE TABLE IF NOT EXISTS adj_reconciliation (
    symbol VARCHAR PRIMARY KEY, n_bars BIGINT, max_abs_diff_pct DOUBLE,
    within_tolerance BOOLEAN, checked_at TIMESTAMP
);
"""


def load_universe(path: str) -> list[str]:
    df = pd.read_csv(path, comment="#")
    return [s.strip().upper() for s in df["symbol"].dropna().tolist()]


def store_events(con, df: pd.DataFrame, kind: str, value_col: str) -> None:
    if df.empty:
        return
    frame = pd.DataFrame({
        "symbol": df["symbol"], "ex_date": df["ex_date"], "kind": kind,
        "value": df[value_col], "source": PROVIDER,
        "ingested_at": _utc_now(),
    })
    con.register("tmp_ev", frame)
    con.execute("""INSERT INTO corporate_actions SELECT * FROM tmp_ev
                   ON CONFLICT (symbol, ex_date, kind, source)
                   DO UPDATE SET value=excluded.value,
                                 ingested_at=excluded.ingested_at;""")
    con.unregister("tmp_ev")


def reconcile(con, prices: pd.DataFrame, divs: pd.DataFrame,
              splits: pd.DataFrame, symbol: str) -> tuple[float, bool]:
    """Recompute adjustment from events; compare to the vendor's adj_close."""
    base = prices[["symbol", "date", "close"]].copy()
    ours = adjust.apply_adjustments(base, dividends=divs, splits=splits)
    # adjust.py normalises `date` to datetime64; the ingestor yields date
    # objects. Merging the two dtypes raises, so normalise BOTH sides here.
    ours = ours.assign(date=pd.to_datetime(ours["date"]))
    vend = prices[["date", "adj_close"]].rename(columns={"adj_close": "vendor"})
    vend = vend.assign(date=pd.to_datetime(vend["date"]))
    merged = ours.merge(vend, on="date", how="inner").dropna(subset=["vendor"])
    if merged.empty:
        return float("nan"), True
    # compare RATIOS, not levels: both series are only defined up to a constant
    ours_r = merged["adj_close"] / merged["adj_close"].iloc[-1]
    vend_r = merged["vendor"] / merged["vendor"].iloc[-1]
    diff = (ours_r - vend_r).abs() / vend_r.replace(0, pd.NA)
    max_diff = float(diff.max(skipna=True) or 0.0)
    ok = max_diff <= RECON_TOLERANCE
    con.execute("""INSERT INTO adj_reconciliation VALUES (?,?,?,?,?)
                   ON CONFLICT (symbol) DO UPDATE SET
                     n_bars=excluded.n_bars,
                     max_abs_diff_pct=excluded.max_abs_diff_pct,
                     within_tolerance=excluded.within_tolerance,
                     checked_at=excluded.checked_at;""",
                [symbol, len(merged), max_diff, ok,
                 _utc_now()])
    return max_diff, ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=48,
                    help="max calls per pass; provider cap is 50/rolling hour")
    ap.add_argument("--auto-wait", action="store_true",
                    help="on hourly cap, sleep ~61min and continue "
                         "until the universe is complete")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--universe", default=UNIVERSE)
    args = ap.parse_args()

    con = store.connect(DB)
    budget.init(con)
    con.execute(EVENT_SCHEMA)

    syms = load_universe(args.universe)
    budget.seed_tasks(con, PROVIDER, ENDPOINT, syms)

    print("=" * 70)
    print(f"TIINGO BACKFILL   universe={len(syms)}   one call per symbol")
    print("=" * 70)

    if args.status:
        for ep, done, pending, failed, restricted, total in budget.progress(con, PROVIDER):
            print(f"  {ep:8s} done={done:4d} pending={pending:4d} failed={failed:3d} "
                  f"restricted={restricted:3d} total={total:4d}")
        bad = con.execute("""SELECT symbol, max_abs_diff_pct FROM adj_reconciliation
                             WHERE NOT within_tolerance ORDER BY 2 DESC""").fetchall()
        print(f"\n  reconciliation: {len(bad)} symbol(s) diverge from vendor adjClose")
        for s, d in bad[:15]:
            print(f"    {s:8s} max diff {d:.3%}")
        con.close()
        return 0

    key = tiingo.get_key()
    if not key:
        print("  no Tiingo key in Credential Manager; aborting")
        return 2

    consecutive = 0
    total_done = 0
    while True:
        left = min(args.cap,
                   budget.remaining_in_window(con, PROVIDER, HOURLY_CAP, WINDOW_MIN))
        used = budget.calls_used_in_window(con, PROVIDER, WINDOW_MIN)
        print(f"  rolling-hour usage: {used}/{HOURLY_CAP}  ->  "
              f"{left} calls available this pass\n")
        if left == 0:
            wait = budget.seconds_until_slot_free(
                con, PROVIDER, HOURLY_CAP, WINDOW_MIN)
            pending_now = sum(row[2] for row in budget.progress(con, PROVIDER))
            if pending_now == 0:
                print("  UNIVERSE COMPLETE.")
                break
            if not args.auto_wait:
                print(f"  Hourly window is full. {pending_now} symbols remain; "
                      f"capacity frees in ~{wait // 60} min.")
                print("  Re-run then, or use --auto-wait to continue automatically.")
                break
            print(f"  Window full; sleeping {wait // 60} min "
                  f"{wait % 60}s (Ctrl+C to stop safely)...")
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                print("\n  stopped during wait -- state checkpointed.")
                break
            continue
        hit_hourly = False
        try:
            left, hit_hourly, consecutive, n = _drain(
                con, key, left, consecutive, args)
            total_done += n
        except KeyboardInterrupt:
            print("\n\n  interrupted by user -- state is checkpointed, "
                  "nothing lost. Re-run to resume.")
            break
        pending = sum(row[2] for row in budget.progress(con, PROVIDER))
        if pending == 0:
            print("\n  UNIVERSE COMPLETE.")
            break
        if not hit_hourly:
            break
        if not args.auto_wait:
            print(f"\n  Hourly limit reached. {pending} symbols remain.")
            print("  Re-run in ~1 hour, or use --auto-wait to continue "
                  "automatically.")
            break
        wait = budget.seconds_until_slot_free(
            con, PROVIDER, HOURLY_CAP, WINDOW_MIN)
        print(f"\n  Hourly cap hit; {pending} remain. "
              f"Sleeping {wait // 60} min {wait % 60}s (Ctrl+C to stop safely)...")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            print("\n  stopped during wait -- state checkpointed.")
            break

    return _finish(con)


def _drain(con, key, left, consecutive, args):
    """One pass until budget/hourly cap. Returns (left, hit_hourly, consec, done)."""
    done = 0
    hit_hourly = False
    while left > 0:
        tasks = budget.next_tasks(con, PROVIDER, ENDPOINT, 1)
        if not tasks:
            break
        t = tasks[0]
        try:
            payload = tiingo.fetch_daily_raw(t.symbol, key=key)
            prices = tiingo.parse_daily(payload, t.symbol)
            divs = tiingo.extract_dividends(payload, t.symbol)
            splits = tiingo.extract_splits(payload, t.symbol)

            store.upsert_prices(con, prices, source=PROVIDER)
            store_events(con, divs, "dividend", "amount")
            store_events(con, splits, "split", "ratio")
            diff, ok = reconcile(con, prices, divs, splits, t.symbol)

            budget.mark_done(con, t)
            budget.record_call(con, PROVIDER, ENDPOINT, t.symbol, True, "200")
            flag = "" if ok else f"  !! RECON DIVERGES {diff:.2%}"
            print(f"    OK   {t.symbol:8s} {len(prices):5d} rows  "
                  f"{len(divs):3d} divs  {len(splits):2d} splits{flag}")
            consecutive = 0
            done += 1
        except Exception as e:
            code = getattr(e, "status_code", None)
            budget.record_call(con, PROVIDER, ENDPOINT, t.symbol, False, str(code))
            if code == 204:
                budget.mark_restricted(con, t, "no rows in window (listed later)")
                print(f"    SKIP {t.symbol:8s} no history in window (listed after start)")
                consecutive = 0
            elif budget.is_plan_restricted(code):
                budget.mark_restricted(con, t, f"{code}: {e}")
                print(f"    SKIP {t.symbol:8s} {code} not on plan")
                consecutive = 0
            elif _is_hourly_limit(e):
                # provider-side cap, not this symbol's fault -> no attempt penalty
                budget.requeue(con, t)
                print(f"    STOP {t.symbol:8s} hourly cap reached (requeued, "
                      f"no penalty)")
                hit_hourly = True
                break
            else:
                budget.mark_failed(con, t, f"{code}: {e}")
                consecutive += 1
                print(f"    FAIL {t.symbol:8s} {code} {str(e)[:70]}")
                if code == 429:
                    print(f"         throttled; backing off {THROTTLE_BACKOFF_S}s")
                    time.sleep(THROTTLE_BACKOFF_S)
                if consecutive >= CONSECUTIVE_FAIL_ABORT:
                    print("\n  !! CIRCUIT BREAKER: consecutive failures; stopping "
                          "to preserve quota. Re-run later to resume.")
                    break
        left -= 1
        time.sleep(SLEEP_S)
    return left, hit_hourly, consecutive, done


def _report(con):
    for ep, done, pending, failed, restricted, total in budget.progress(con, PROVIDER):
        pct = done / total * 100 if total else 0
        print(f"\n  {ep}: done={done} pending={pending} failed={failed} "
              f"restricted={restricted} total={total} ({pct:.0f}%)")
    bad = con.execute("SELECT count(*) FROM adj_reconciliation "
                      "WHERE NOT within_tolerance").fetchone()[0]
    print(f"  reconciliation divergences so far: {bad} "
          f"(investigate with scripts/inspect_divergence.py)")


def _finish(con):
    _report(con)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


