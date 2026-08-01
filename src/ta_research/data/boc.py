"""Bank of Canada Valet FX ingestor (official, free, no key).

FXUSDCAD = Canadian dollars per 1 US dollar -- the direct USD->CAD multiplier
the CAD-accounting book needs.

Empirical finding from the first slice run: FXUSDCAD only reaches 2017-01-03.
BoC discontinued the noon-rate regime in 2017 and started the current daily
indicative series then. Research needs history back to 2010, so pre-2017 comes
from a legacy noon-rate series and the two are stitched. Legacy series naming is
verified empirically by scripts/source_diag.py rather than assumed here.
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://www.bankofcanada.ca/valet/observations"
TIMEOUT = 30
CURRENT_SERIES = "FXUSDCAD"
# CONFIRMED by source_diag 2026-07-23: IEXE0101 (USD_NOON) covers
# 2010-01-04..2017-04-28, complementing FXUSDCAD (2017-01-03->).
# The two overlap 2017-01-03..2017-04-28, which validate_overlap() exploits.
LEGACY_SERIES = "IEXE0101"
LEGACY_CANDIDATES = [LEGACY_SERIES]


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


def fetch_fx(series: str = CURRENT_SERIES, start: str = "2010-01-01") -> pd.DataFrame:
    url = f"{BASE}/{series}/json"
    r = requests.get(url, params={"start_date": start}, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_valet(r.json(), series)


def stitch_fx(legacy: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Join a legacy pre-2017 series to the current one.

    The current series wins on any overlapping date (it is the live regime);
    legacy only fills dates the current series does not cover. Output is sorted,
    de-duplicated, and carries `series_used` so the provenance of every pre-2017
    rate stays auditable -- a stitched FX series that hides its seams would let a
    regime change masquerade as a market move.
    """
    cur = current.copy()
    cur["series_used"] = "current"
    if legacy is None or legacy.empty:
        return cur.sort_values("date").reset_index(drop=True)
    leg = legacy.copy()
    leg["series_used"] = "legacy"
    leg = leg[~leg["date"].isin(set(cur["date"]))]
    out = pd.concat([leg, cur], ignore_index=True)
    return out.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def validate_overlap(legacy: pd.DataFrame, current: pd.DataFrame,
                     tolerance: float = 0.01) -> pd.DataFrame:
    """Compare the two FX regimes on their overlapping dates.

    IEXE0101 is a NOON rate; FXUSDCAD is a daily indicative rate. They are
    different methodologies, so small differences are expected and fine -- but a
    large or drifting gap would mean the stitch injects a fake jump at the seam,
    which a momentum model would happily read as a signal. Returns the overlap
    rows with `diff_pct` and a boolean `within_tolerance`.
    """
    if legacy is None or legacy.empty or current is None or current.empty:
        return pd.DataFrame(columns=["date", "legacy", "current",
                                     "diff_pct", "within_tolerance"])
    merged = legacy.merge(current, on="date", suffixes=("_legacy", "_current"))
    if merged.empty:
        return pd.DataFrame(columns=["date", "legacy", "current",
                                     "diff_pct", "within_tolerance"])
    merged["diff_pct"] = (
        (merged["rate_current"] - merged["rate_legacy"]).abs() / merged["rate_legacy"]
    )
    merged["within_tolerance"] = merged["diff_pct"] <= tolerance
    return merged.rename(columns={"rate_legacy": "legacy", "rate_current": "current"})[
        ["date", "legacy", "current", "diff_pct", "within_tolerance"]
    ]


def fetch_stitched_fx(start: str = "2010-01-01") -> pd.DataFrame:
    """Full USD->CAD history 2010-> by joining the legacy noon series to current."""
    current = fetch_fx(CURRENT_SERIES, start=start)
    try:
        legacy = fetch_fx(LEGACY_SERIES, start=start)
    except Exception:
        legacy = pd.DataFrame()
    return stitch_fx(legacy, current)
