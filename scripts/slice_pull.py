"""Vertical-slice pull (Step 3 instrument).

Pulls a small US+CA sample from Stooq and FMP, plus BoC FX, into DuckDB, then
runs the quality suite on what landed. Its console report answers the questions
that gate the full-universe code:
  * Does FMP-stable free cover TSX/CA names?
  * Which Stooq ticker convention works for CA?
  * Any rate-limit headers / 429s across a burst of FMP calls?
  * Exact field names FMP returns (printed once).
Real data on disk AND the empirical answers, in one run. Report the output back.
"""
from __future__ import annotations


from ta_research.data import boc, fmp, stooq
from ta_research.quality import checks
from ta_research.storage import duckdb_store as store

DB = "data/research.duckdb"

# (app_symbol, market, exchange_for_calendar)
SAMPLE = [
    ("AAPL", "US", "NYSE"),
    ("MSFT", "US", "NYSE"),
    ("SPY",  "US", "NYSE"),
    ("RY.TO",   "CA", "TSX"),
    ("SHOP.TO", "CA", "TSX"),
]


def hr(t): print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def main() -> int:
    con = store.connect(DB)
    hr("STOOQ PULL (quota-free primary)")
    for sym, mkt, _ in SAMPLE:
        try:
            df = stooq.fetch_prices(sym, mkt)
            n = store.upsert_prices(con, df, source="stooq")
            store.log_ingest(con, "stooq", sym, n, True,
                             f"{stooq.stooq_symbol(sym, mkt)} {df['date'].min()}..{df['date'].max()}")
            print(f"  OK  {sym:8s} -> {stooq.stooq_symbol(sym, mkt):10s} {n:5d} rows "
                  f"{df['date'].min()}..{df['date'].max()}")
        except Exception as e:
            store.log_ingest(con, "stooq", sym, 0, False, repr(e))
            print(f"  FAIL {sym:8s} -> {stooq.stooq_symbol(sym, mkt):10s} {e}")

    hr("FMP-STABLE PULL (coverage + quota probe)")
    key = fmp.get_key()
    if not key:
        print("  no FMP key; skipping FMP cross-check (Stooq still carries the slice).")
    else:
        printed_fields = False
        for sym, mkt, _ in SAMPLE:
            try:
                df = fmp.fetch_prices(sym.replace(".TO", ".TO"))  # FMP TSX uses .TO suffix
                if not printed_fields:
                    print(f"  FMP fields for {sym}: {list(df.columns)}")
                    printed_fields = True
                n = store.upsert_prices(con, df, source="fmp")
                has_adj = df["adj_close"].notna().any()
                store.log_ingest(con, "fmp", sym, n, True, f"adj_close={has_adj}")
                print(f"  OK  {sym:8s} {n:5d} rows  adj_close={has_adj}  "
                      f"{df['date'].min()}..{df['date'].max()}")
            except Exception as e:
                store.log_ingest(con, "fmp", sym, 0, False, repr(e))
                print(f"  FAIL {sym:8s} {e}")

    hr("BANK OF CANADA FX (FXUSDCAD = CAD per 1 USD)")
    try:
        fx = boc.fetch_fx()
        n = store.upsert_fx(con, fx, series="FXUSDCAD", source="boc")
        print(f"  OK  {n} obs  {fx['date'].min()}..{fx['date'].max()}  "
              f"latest rate={fx.sort_values('date')['rate'].iloc[-1]}")
    except Exception as e:
        print(f"  FAIL {e}")

    hr("QUALITY SUITE on landed data (per symbol, Stooq source)")
    for sym, mkt, exch in SAMPLE:
        rows = con.execute(
            "SELECT symbol,date,open,high,low,close,volume FROM prices_raw "
            "WHERE symbol=? AND source='stooq' ORDER BY date", [sym]
        ).df()
        if rows.empty:
            print(f"  {sym:8s} no stooq rows landed")
            continue
        issues = checks.run_all(rows, exch)
        if not issues:
            print(f"  {sym:8s} CLEAN ({len(rows)} rows)")
        else:
            for i in issues:
                print(f"  {sym:8s} [{i.check}] {i.detail}")

    hr("INGEST LOG SUMMARY")
    summary = con.execute(
        "SELECT source, count(*) filter (where ok) ok_n, "
        "count(*) filter (where not ok) fail_n, sum(rows) AS total_rows "
        "FROM ingest_log GROUP BY source"
    ).df()
    print(summary.to_string(index=False))
    print("\nPaste this whole report back to unlock the full-universe acquisition code.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
