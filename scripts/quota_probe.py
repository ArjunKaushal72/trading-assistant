"""Quota / batch probe -- the last unknown before the universe builder.

FMP free is ~250 calls/day. A 300-400 name universe needs one call per symbol
per endpoint, so a naive daily refresh would exceed the quota on day one. This
probe measures the real limit and looks for batch endpoints that would collapse
a whole-universe daily update into a handful of calls.

It deliberately makes a burst of small requests and reports rate-limit headers
and the first 429. Safe to run: it uses tiny date windows.
"""
from __future__ import annotations

import time
from datetime import datetime

import requests

from ta_research.data import fmp

TIMEOUT = 25
BURST = 12


def hr(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def main() -> int:
    key = fmp.get_key()
    if not key:
        print("no FMP key found; aborting")
        return 2
    print("=" * 70)
    print(f"FMP QUOTA / BATCH PROBE  {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 70)

    hr("A  Rate-limit headers + burst behaviour")
    syms = ["AAPL", "MSFT", "SPY", "GOOGL", "AMZN", "META",
            "NVDA", "JPM", "XOM", "JNJ", "PG", "KO"]
    for i, s in enumerate(syms[:BURST], 1):
        try:
            r = requests.get(fmp.STABLE_EOD, params={
                "symbol": s, "from": "2026-07-01", "to": "2026-07-10",
                "apikey": key}, timeout=TIMEOUT)
            hdrs = {k: v for k, v in r.headers.items()
                    if "rate" in k.lower() or "limit" in k.lower() or k.lower() == "retry-after"}
            print(f"  {i:2d}. {s:6s} HTTP {r.status_code}  headers={hdrs or '{}'}")
            if r.status_code == 429:
                print("      -> hit 429; stopping burst")
                break
        except Exception as e:
            print(f"  {i:2d}. {s:6s} ERROR {e!r}")
        time.sleep(0.3)

    hr("B  Batch endpoint candidates (would collapse daily refresh to few calls)")
    candidates = [
        ("batch-quote", "https://financialmodelingprep.com/stable/batch-quote",
         {"symbols": "AAPL,MSFT,SPY"}),
        ("batch-eod", "https://financialmodelingprep.com/stable/batch-eod",
         {"date": "2026-07-22"}),
        ("batch-historical-eod",
         "https://financialmodelingprep.com/stable/batch-historical-eod",
         {"symbols": "AAPL,MSFT", "from": "2026-07-01", "to": "2026-07-10"}),
        ("multi-symbol full",
         fmp.STABLE_EOD, {"symbol": "AAPL,MSFT", "from": "2026-07-01",
                          "to": "2026-07-10"}),
    ]
    for name, url, params in candidates:
        try:
            r = requests.get(url, params={**params, "apikey": key}, timeout=TIMEOUT)
            body = r.text[:160].replace("\n", " ")
            print(f"  [{name:22s}] HTTP {r.status_code}  {body!r}")
        except Exception as e:
            print(f"  [{name:22s}] ERROR {e!r}")

    hr("VERDICT")
    print("  If a batch endpoint returned 200 with multi-symbol data, the daily")
    print("  refresh fits the free quota easily. If not, the backfill must be")
    print("  spread over several days with resumable checkpointing, and the")
    print("  universe size becomes quota-bound -- report back either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
