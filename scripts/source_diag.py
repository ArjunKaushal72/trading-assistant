"""Source diagnostic -- resolves the unknowns the first slice run exposed.

Four questions, answered empirically on YOUR network:
  1. Stooq: does a browser User-Agent fix the 404? Which host/param variant works?
  2. FMP: what are the RAW payload keys (the previous run printed parsed columns,
     a reporting bug), and is there any free adjusted-close or dividend endpoint?
  3. FMP: which dividend/split endpoints are free? (We must build total-return
     adjustment ourselves -- see src/ta_research/data/adjust.py.)
  4. BoC: which legacy FX series reaches back to 2010?

Prints a verdict per question. Report the whole output back.
"""
from __future__ import annotations

import json
from datetime import datetime

import requests

from ta_research.data import fmp, stooq

TIMEOUT = 25


def hr(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def q1_stooq():
    hr("Q1  STOOQ -- User-Agent / host / param variants")
    variants = [
        ("com + UA + dates", stooq.HOSTS[0], stooq.HEADERS, True),
        ("com + UA + no dates", stooq.HOSTS[0], stooq.HEADERS, False),
        ("com + NO UA + dates", stooq.HOSTS[0], {}, True),
        ("pl  + UA + dates", stooq.HOSTS[1], stooq.HEADERS, True),
    ]
    winner = None
    for name, host, hdrs, use_dates in variants:
        params = stooq.build_url_params("aapl.us",
                                        "2010-01-01" if use_dates else None,
                                        "2026-07-23" if use_dates else None)
        try:
            r = requests.get(host, params=params, headers=hdrs, timeout=TIMEOUT)
            head = r.text[:60].replace("\n", "\\n")
            print(f"  [{name}] HTTP {r.status_code}  len={len(r.text)}  head={head!r}")
            if r.status_code == 200 and "Date" in r.text[:200]:
                winner = winner or name
        except Exception as e:
            print(f"  [{name}] ERROR {e!r}")
    print(f"\n  --> Stooq working variant: {winner or 'NONE -- Stooq unusable on this network'}")
    if winner:
        for sym, mkt in [("AAPL", "US"), ("RY.TO", "CA"), ("SHOP.TO", "CA"),
                         ("ENB.TO", "CA"), ("XIU.TO", "CA")]:
            try:
                df = stooq.fetch_prices(sym, mkt)
                print(f"     {sym:8s} -> {stooq.stooq_symbol(sym, mkt):10s} "
                      f"{len(df):5d} rows {df['date'].min()}..{df['date'].max()}")
            except Exception as e:
                print(f"     {sym:8s} -> {stooq.stooq_symbol(sym, mkt):10s} FAIL {e}")
    return winner


def q2_fmp_raw():
    hr("Q2  FMP -- RAW payload keys (previous run printed parsed cols: my bug)")
    key = fmp.get_key()
    if not key:
        print("  no key; skipping")
        return
    url = fmp.STABLE_EOD
    r = requests.get(url, params={"symbol": "AAPL", "from": "2024-01-02",
                                  "to": "2024-01-10", "apikey": key}, timeout=TIMEOUT)
    print(f"  HTTP {r.status_code}")
    if r.status_code == 200:
        payload = r.json()
        sample = payload[0] if isinstance(payload, list) and payload else payload
        print(f"  RAW keys: {list(sample.keys()) if isinstance(sample, dict) else type(sample)}")
        print(f"  sample row: {json.dumps(sample)[:300]}")


def q3_fmp_events():
    hr("Q3  FMP -- which dividend / split / adjusted endpoints are FREE?")
    key = fmp.get_key()
    if not key:
        print("  no key; skipping")
        return
    candidates = [
        ("dividends", "https://financialmodelingprep.com/stable/dividends"),
        ("splits", "https://financialmodelingprep.com/stable/splits"),
        ("eod dividend-adjusted",
         "https://financialmodelingprep.com/stable/historical-price-eod/dividend-adjusted"),
    ]
    for name, url in candidates:
        try:
            r = requests.get(url, params={"symbol": "AAPL", "apikey": key}, timeout=TIMEOUT)
            body = r.text[:150].replace("\n", " ")
            print(f"  [{name}] HTTP {r.status_code}  {body!r}")
        except Exception as e:
            print(f"  [{name}] ERROR {e!r}")


def q4_boc_legacy():
    hr("Q4  BoC -- which series reaches back to 2010?")
    from ta_research.data import boc
    for series in [boc.CURRENT_SERIES] + boc.LEGACY_CANDIDATES:
        try:
            df = boc.fetch_fx(series, start="2010-01-01")
            print(f"  [{series:20s}] {len(df):5d} obs  {df['date'].min()}..{df['date'].max()}")
        except Exception as e:
            print(f"  [{series:20s}] FAIL {str(e)[:90]}")
    # also list what the Valet catalogue offers for USD/CAD
    try:
        r = requests.get("https://www.bankofcanada.ca/valet/lists/series/json", timeout=TIMEOUT)
        if r.status_code == 200:
            alls = r.json().get("series", {})
            hits = {k: v.get("label", "") for k, v in alls.items()
                    if "USD" in k.upper() or "usd" in str(v.get("label", "")).lower()}
            print(f"\n  Valet series mentioning USD ({len(hits)} found, first 15):")
            for k, v in list(hits.items())[:15]:
                print(f"     {k:22s} {v}")
    except Exception as e:
        print(f"  catalogue lookup failed: {e!r}")


def main() -> int:
    print("=" * 70)
    print(f"SOURCE DIAGNOSTIC  {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 70)
    q1_stooq()
    q2_fmp_raw()
    q3_fmp_events()
    q4_boc_legacy()
    print("\n" + "=" * 70)
    print("Report this entire output back. It decides the CA data source and the")
    print("total-return adjustment path -- both blockers for the universe builder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
