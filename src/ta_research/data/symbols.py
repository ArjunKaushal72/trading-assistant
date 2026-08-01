"""Per-provider symbol conventions.

Empirically observed (provider_probe 2026-07-23): Tiingo 404s on "BRK.B" but
serves "BRK-B"; Twelve Data accepts "BRK.B". A single canonical symbol therefore
cannot be passed to every provider verbatim. Getting this wrong looks exactly
like a coverage gap, which is how a class-share convention nearly got recorded
as "Tiingo doesn't have Berkshire".

Canonical form in this project = the FMP/Yahoo style with a hyphen (BRK-B).
"""
from __future__ import annotations

# Explicit exceptions first; the general rule handles the rest.
TIINGO_OVERRIDES: dict[str, str] = {}
TWELVEDATA_OVERRIDES: dict[str, str] = {}


def to_tiingo(symbol: str) -> str:
    s = symbol.upper().strip()
    if s in TIINGO_OVERRIDES:
        return TIINGO_OVERRIDES[s]
    return s.replace(".", "-")          # BRK.B -> BRK-B


def to_twelvedata(symbol: str) -> str:
    s = symbol.upper().strip()
    if s in TWELVEDATA_OVERRIDES:
        return TWELVEDATA_OVERRIDES[s]
    return s.replace("-", ".")          # BRK-B -> BRK.B


def to_fmp(symbol: str) -> str:
    return symbol.upper().strip().replace(".", "-")
