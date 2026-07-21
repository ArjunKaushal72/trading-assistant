"""Bank of Canada Valet FX ingestor (official, free, no key).

FXUSDCAD = Canadian dollars per 1 US dollar -- the direct multiplier to convert
a USD price to CAD, which is what the CAD-accounting book needs. (Earlier config
named FXCADUSD, the inverse; FXUSDCAD is correct for USD->CAD conversion.)
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://www.bankofcanada.ca/valet/observations"
TIMEOUT = 30
DEFAULT_SERIES = "FXUSDCAD"


def parse_valet(payload: dict, series: str) -> pd.DataFrame:
    obs = payload.get("observations", [])
    rows = []
    for o in obs:
        d = o.get("d")
        cell = o.get(series)
        if d and isinstance(cell, dict) and cell.get("v") not in (None, ""):
            rows.append({"date": pd.to_datetime(d).date(),
                         "rate": pd.to_numeric(cell["v"], errors="coerce")})
    if not rows:
        raise ValueError(f"boc: no observations for {series}")
    return pd.DataFrame(rows)


def fetch_fx(series: str = DEFAULT_SERIES, start: str = "2010-01-01") -> pd.DataFrame:
    url = f"{BASE}/{series}/json"
    r = requests.get(url, params={"start_date": start}, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_valet(r.json(), series)
