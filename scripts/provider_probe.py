"""Alternative free-provider coverage probe.

Why: FMP's free tier whitelists symbols -- 41 of our 214 universe returned data,
173 returned HTTP 402. A 41-name universe cannot support cross-sectional ranking
research, so before spending money we test whether a different free tier covers
the full universe (and possibly Canadian listings too, which would reopen the
CA question at $0).

Candidates, both free-tier, both no-card:
  * Tiingo      -- generous EOD history, broad US coverage
  * Twelve Data -- 800 calls/day, 8/min, US + international incl. TSX

Get keys (free, ~2 min each), store them, then run:
    python -m keyring set trading-assistant tiingo_api_key
    python -m keyring set trading-assistant twelvedata_api_key
    uv run python scripts/provider_probe.py

Probes only ~10 symbols per provider, so it costs almost nothing.
"""
from __future__ import annotations

import time
from datetime import datetime

import requests

try:
    import keyring
except Exception:
    keyring = None

SERVICE = "trading-assistant"
TIMEOUT = 25

# Deliberately mixes symbols FMP free ALLOWED (AAPL, KO) with ones it BLOCKED
# (PG, HD, QQQ, XLK, VOO) so coverage differences show up immediately.
TEST_US = ["AAPL", "KO", "PG", "HD", "QQQ", "XLK", "VOO", "BRK.B"]
TEST_CA = ["RY.TO", "SHOP.TO", "XIU.TO"]


def hr(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def key(name: str) -> str | None:
    if keyring is None:
        return None
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def probe_tiingo(k: str) -> None:
    hr("TIINGO  (free tier)")
    ok = blocked = 0
    for sym in TEST_US:
        url = f"https://api.tiingo.com/tiingo/daily/{sym}/prices"
        try:
            r = requests.get(url, params={"startDate": "2010-01-04",
                                          "endDate": "2010-01-20", "token": k},
                             headers={"Content-Type": "application/json"},
                             timeout=TIMEOUT)
            n = len(r.json()) if r.status_code == 200 else 0
            flag = "OK  " if r.status_code == 200 and n else "FAIL"
            if flag == "OK  ":
                ok += 1
            else:
                blocked += 1
            print(f"  {flag} {sym:8s} HTTP {r.status_code}  rows={n}  "
                  f"{r.text[:80] if r.status_code != 200 else ''}")
        except Exception as e:
            blocked += 1
            print(f"  ERR  {sym:8s} {e!r}")
        time.sleep(1.0)
    print(f"\n  --> Tiingo US coverage: {ok}/{len(TEST_US)} available")


def probe_twelvedata(k: str) -> None:
    hr("TWELVE DATA  (free tier -- also test Canadian listings)")
    for label, syms in (("US", TEST_US), ("CA", TEST_CA)):
        ok = 0
        for sym in syms:
            try:
                r = requests.get("https://api.twelvedata.com/time_series",
                                 params={"symbol": sym, "interval": "1day",
                                         "start_date": "2010-01-04",
                                         "end_date": "2010-01-20",
                                         "apikey": k}, timeout=TIMEOUT)
                j = r.json() if r.status_code == 200 else {}
                status = j.get("status", "")
                n = len(j.get("values", []) or [])
                good = status == "ok" and n > 0
                ok += 1 if good else 0
                print(f"  {'OK  ' if good else 'FAIL'} {label} {sym:9s} "
                      f"HTTP {r.status_code} status={status} rows={n} "
                      f"{str(j.get('message',''))[:70]}")
            except Exception as e:
                print(f"  ERR  {label} {sym:9s} {e!r}")
            time.sleep(8.0)   # free tier is 8 calls/min
        print(f"  --> Twelve Data {label} coverage: {ok}/{len(syms)}\n")


def main() -> int:
    print("=" * 70)
    print(f"PROVIDER COVERAGE PROBE  {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 70)
    print("Testing symbols FMP free BLOCKED (PG, HD, QQQ, XLK, VOO) alongside")
    print("ones it allowed (AAPL, KO). Full coverage => free path stays open.")

    t = key("tiingo_api_key")
    if t:
        probe_tiingo(t)
    else:
        print("\n  [tiingo] no key stored -- get one free at tiingo.com, then:")
        print("           python -m keyring set trading-assistant tiingo_api_key")

    td = key("twelvedata_api_key")
    if td:
        probe_twelvedata(td)
    else:
        print("\n  [twelvedata] no key stored -- free at twelvedata.com, then:")
        print("               python -m keyring set trading-assistant twelvedata_api_key")

    hr("VERDICT")
    print("  Full coverage on a free tier  -> switch provider, stay at $0.")
    print("  Partial/blocked everywhere    -> FMP paid (~US$22-29/mo) is the")
    print("                                   honest call; it is inside budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
