"""FMP free-tier capability probe (Step 2 of kickoff).

Empirically checks what the FMP free tier returns *today* on THIS machine and
key, then prints a verdict that selects the acquisition branch:

  * Stooq-primary (assumed) -- FMP usable only as secondary / fundamentals.
  * FMP-secondary-viable    -- FMP returns enough clean history to cross-check.

It asserts nothing about FMP's schema; endpoints drift, so the probe tries a
few candidate URLs and REPORTS what responds. No network mocking, no key in
source: the key is read from Windows Credential Manager via keyring.

Store your free key first (PowerShell), then run:
    python -m keyring set trading-assistant fmp_api_key
    python scripts/fmp_probe.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime

import requests

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None

SERVICE = "trading-assistant"
KEY_USER = "fmp_api_key"
TEST_SYMBOL = "AAPL"
HISTORY_START = "2010-01-01"
TIMEOUT = 20

# Candidate historical-price endpoints, tried in order. If FMP has moved the
# free tier, the probe will show which (if any) still respond.
CANDIDATES = [
    ("v3 historical-price-full",
     "https://financialmodelingprep.com/api/v3/historical-price-full/{sym}"
     "?from={start}&to={end}&apikey={key}"),
    ("stable historical-price-eod/full",
     "https://financialmodelingprep.com/stable/historical-price-eod/full"
     "?symbol={sym}&from={start}&to={end}&apikey={key}"),
]


def get_key() -> str | None:
    if keyring is None:
        print("  ! keyring not importable; cannot read Credential Manager.")
        return None
    try:
        return keyring.get_password(SERVICE, KEY_USER)
    except Exception as e:  # pragma: no cover
        print(f"  ! keyring error: {e}")
        return None


def _extract_series(payload: object) -> list[dict]:
    """FMP has returned either {'historical': [...]} or a bare list over time."""
    if isinstance(payload, dict):
        for k in ("historical", "results", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
        return []
    if isinstance(payload, list):
        return payload
    return []


def probe_endpoint(name: str, url_tmpl: str, key: str) -> dict:
    end = datetime.today().strftime("%Y-%m-%d")
    url = url_tmpl.format(sym=TEST_SYMBOL, start=HISTORY_START, end=end, key=key)
    safe = url.replace(key, "****")
    result = {"name": name, "url": safe, "ok": False}
    t0 = time.time()
    try:
        r = requests.get(url, timeout=TIMEOUT)
        result["status"] = r.status_code
        result["elapsed_s"] = round(time.time() - t0, 2)
        for h in ("X-Rate-Limit-Remaining", "X-Rate-Limit-Limit", "Retry-After"):
            if h in r.headers:
                result[h] = r.headers[h]
        if r.status_code != 200:
            result["body_head"] = r.text[:200]
            return result
        series = _extract_series(r.json())
        result["rows"] = len(series)
        if series:
            dates = [row.get("date") for row in series if row.get("date")]
            if dates:
                result["earliest"] = min(dates)
                result["latest"] = max(dates)
                result["reaches_2010"] = min(dates) <= "2010-12-31"
        result["ok"] = result.get("rows", 0) > 0
    except Exception as e:
        result["error"] = repr(e)
    return result


def main() -> int:
    print("=" * 68)
    print("FMP FREE-TIER CAPABILITY PROBE")
    print(f"machine time: {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 68)

    key = get_key()
    if not key:
        print("\nNO FMP KEY FOUND in Credential Manager.")
        print("Store it, then re-run:")
        print("    python -m keyring set trading-assistant fmp_api_key")
        print("\nStooq needs no key, so this only blocks the FMP cross-check,")
        print("not the pipeline. Report this output back either way.")
        return 2

    print(f"key loaded from Credential Manager (len={len(key)}). Probing {TEST_SYMBOL}...\n")
    results = [probe_endpoint(n, u, key) for n, u in CANDIDATES]

    for res in results:
        print(f"[{res['name']}]")
        for k, v in res.items():
            if k == "name":
                continue
            print(f"    {k}: {v}")
        print()

    working = [r for r in results if r.get("ok")]
    print("-" * 68)
    if not working:
        print("VERDICT: FMP free tier returned no usable history on this key today.")
        print("BRANCH:  Stooq-primary CONFIRMED. FMP demoted to fundamentals-only")
        print("         (probe fundamentals separately later). Cross-check will use")
        print("         a second free source instead. This is an acceptable outcome.")
    else:
        best = max(working, key=lambda r: r.get("rows", 0))
        reaches = best.get("reaches_2010", False)
        print(f"VERDICT: FMP free tier works via [{best['name']}].")
        print(f"         rows={best.get('rows')}, earliest={best.get('earliest')}, "
              f"reaches_2010={reaches}")
        if reaches:
            print("BRANCH:  FMP viable as SECONDARY cross-check back to 2010.")
        else:
            print("BRANCH:  FMP usable but shallow history -> Stooq stays primary for")
            print("         2010 depth; FMP cross-checks only its available window.")
    print("-" * 68)
    print("\nPaste this entire output back to proceed to week-1 acquisition code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
