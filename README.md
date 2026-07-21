# trading-assistant

Local-first, human-in-the-loop trading **research & recommendation** assistant.
Phase 1 = research only. No orders are placed by this software in any phase
until the written autonomy gate is cleared; the broker write-path stays
defined-but-disabled behind a guard.

## Status: Phase 1, Step 1-2 (environment + FMP probe)

## Layout
    src/ta_research/        core library (data, quality, storage)
    scripts/fmp_probe.py    Step 2 capability probe (run once, report output)
    snapshot/               daily point-in-time snapshot job (Step 4)
    research/notebooks/     the 5 research notebooks (weeks 1-3)
    tests/                  quality + calendar suites (green before results trusted)
    config/config.example.toml   copy to config.toml (gitignored); NO secrets here

## Secrets
API keys live in Windows Credential Manager via `keyring`, never in files.
    python -m keyring set trading-assistant fmp_api_key

## Setup (Windows, PowerShell)
    uv sync --extra dev
    uv run pytest
    uv run python scripts/fmp_probe.py

## Non-negotiables (see project charter)
- Capital preservation > explainability > robustness > maintainability > return.
- Every backtest is presumed overfit until it clears the frozen pass/fail bar.
- TFSA active-trading tax risk is a standing flag on any turnover.
