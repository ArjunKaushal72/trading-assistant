"""Investigate reconciliation divergences between our adjustment and the vendor's.

The backfill flags any symbol where independently recomputing total-return
adjustment from dividend/split events disagrees with Tiingo's own adjClose by
more than the tolerance. This script localises WHERE the disagreement starts,
which almost always identifies the missing corporate action.

Known gap this is expected to reveal: adjust.py models dividends and splits
only. SPINOFFS are a third class -- when a parent distributes shares of a new
company, the parent's price drops and vendors adjust history, but no dividend
or split event is recorded. ABT (AbbVie, 2013) and DHR (Envista 2019,
Veralto 2023) are exactly this pattern.

    uv run python scripts/inspect_divergence.py            # all divergent names
    uv run python scripts/inspect_divergence.py ABT DHR    # specific symbols
"""
from __future__ import annotations

import sys

import pandas as pd

from ta_research.data import adjust
from ta_research.storage import duckdb_store as store

DB = "data/research.duckdb"
TOP_N = 8


def divergent_symbols(con) -> list[tuple[str, float]]:
    return con.execute("""
        SELECT symbol, max_abs_diff_pct FROM adj_reconciliation
        WHERE NOT within_tolerance ORDER BY max_abs_diff_pct DESC;
    """).fetchall()


def load(con, symbol: str):
    prices = con.execute("""
        SELECT symbol, date, close, adj_close FROM prices_raw
        WHERE symbol=? AND source='tiingo' ORDER BY date;
    """, [symbol]).df()
    events = con.execute("""
        SELECT ex_date, kind, value FROM corporate_actions
        WHERE symbol=? ORDER BY ex_date;
    """, [symbol]).df()
    return prices, events


def inspect(con, symbol: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {symbol}")
    print("=" * 70)
    prices, events = load(con, symbol)
    if prices.empty:
        print("  no rows stored")
        return

    divs = events[events["kind"] == "dividend"].rename(
        columns={"value": "amount"}).assign(symbol=symbol)[["symbol", "ex_date", "amount"]]
    splits = events[events["kind"] == "split"].rename(
        columns={"value": "ratio"}).assign(symbol=symbol)[["symbol", "ex_date", "ratio"]]

    ours = adjust.apply_adjustments(
        prices[["symbol", "date", "close"]], dividends=divs, splits=splits)
    ours = ours.assign(date=pd.to_datetime(ours["date"]))
    vend = prices[["date", "adj_close"]].assign(date=pd.to_datetime(prices["date"]))
    m = ours.merge(vend, on="date", how="inner").dropna(subset=["adj_close"])
    if m.empty:
        print("  no overlapping vendor adj_close")
        return

    ours_r = m["adj_close_x"] / m["adj_close_x"].iloc[-1]
    vend_r = m["adj_close_y"] / m["adj_close_y"].iloc[-1]
    m = m.assign(diff_pct=((ours_r - vend_r).abs() / vend_r).values)

    # The divergence STARTS at the earliest date where the gap jumps -- that is
    # the date of the corporate action we failed to model.
    m = m.assign(jump=m["diff_pct"].diff().abs())
    worst = m.nlargest(TOP_N, "jump")[["date", "close", "diff_pct", "jump"]]
    print(f"  bars={len(m)}  max divergence={m['diff_pct'].max():.3%}")
    print(f"  dividends={len(divs)}  splits={len(splits)}")
    print("\n  largest single-day jumps in divergence "
          "(these dates are the suspect corporate actions):")
    for _, r in worst.iterrows():
        d = pd.Timestamp(r["date"]).date()
        near = events[(pd.to_datetime(events["ex_date"]) >= pd.Timestamp(d) - pd.Timedelta(days=5)) &
                      (pd.to_datetime(events["ex_date"]) <= pd.Timestamp(d) + pd.Timedelta(days=5))]
        tag = "" if near.empty else f"  <- known event(s): {list(near['kind'])}"
        print(f"    {d}  close={r['close']:9.2f}  divergence={r['diff_pct']:.3%}"
              f"  jump={r['jump']:.3%}{tag}")
    print("\n  A large jump with NO known event nearby = an unmodelled corporate")
    print("  action (most likely a spinoff or a special distribution).")


def main() -> int:
    con = store.connect(DB)
    args = [a.upper() for a in sys.argv[1:]]
    if args:
        targets = args
    else:
        rows = divergent_symbols(con)
        if not rows:
            print("No divergences recorded. Nothing to investigate.")
            con.close()
            return 0
        print(f"{len(rows)} divergent symbol(s):")
        for s, d in rows:
            print(f"   {s:8s} {d:.3%}")
        targets = [s for s, _ in rows]

    for sym in targets:
        inspect(con, sym)

    print("\n" + "=" * 70)
    print("  DECISION GUIDANCE")
    print("=" * 70)
    print("  Tiingo's adjClose is the RESEARCH SERIES -- it accounts for spinoffs")
    print("  our adjust.py does not model. Our recomputation is a CANARY, not the")
    print("  source of truth. Divergent names stay usable; what matters is that")
    print("  the divergence is explained by a real event rather than by a bug.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
