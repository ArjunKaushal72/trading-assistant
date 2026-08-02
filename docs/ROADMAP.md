# Roadmap — repo vs. charter

The goal is to **make money on the market** — net of costs, FX, and tax, without
catastrophic drawdowns (`CHARTER.md` §1). The way you get there is the charter's
ordering: **prove a real edge before risking capital, then let it trade.** So this
roadmap sequences the build around one gate — *does a signal clear the frozen
pass/fail bar?* — and puts everything that touches money behind it.

Status as at 2026-08-01, checked against the actual tree.

## What exists (solid)

The **data plane** and its tests, plus CI:

- `data/` — Tiingo (primary), FMP, Stooq, Bank-of-Canada FX ingestors; `adjust.py`
  (splits/dividends, vendor `adj_close` is the research series), `budget.py`,
  `symbols.py`, `calendars.py` (XTSE/XNYS sessions).
- `quality/checks.py`, `storage/duckdb_store.py` (`prices_raw`, `fx_rates`,
  `ingest_log`; idempotent, point-in-time `ingested_at` from day one).
- 12 green test suites; `.github/workflows/ci.yml`.

This is a mature **research data layer** — not yet the assistant.

## What does NOT exist yet (verified: 0 files)

Questrade integration · backtest engine · any signal/strategy · recommendation
engine · HITL state machine · the desktop app shell (tray daemon, React cockpit,
Python sidecar, SQLite app state).

## The sequence — capital preservation first

| # | Phase | Why here | Gate to advance |
|---|---|---|---|
| **1** | **Backtest engine + correctness suite** ← *building now* | Highest-risk untested code (charter §6). Nothing downstream is trustworthy without it. | Suite green: no look-ahead, costs/FX charged, known-outcome exact |
| **2** | **Baseline signal (gradient-boosted trees)** | Charter's stated baseline "everything must beat." Something to run through #1. | Produces daily target weights on point-in-time features only |
| **3** | **Prove or kill vs the frozen bar** | The **go/no-go**. Sharpe ≥ XEQT+0.15, maxDD ≤ 1.25× XEQT, ≤48 trades/yr, CAD, 2× cost-robust. | Edge clears the bar on the untouched 2023–2025 holdout — or is killed |
| **4** | **Questrade read-only + account sync** | Only worth wiring once an edge exists. Refresh-token rotation, positions/orders/executions. | Read loop + fill detection tested against recorded fixtures |
| **5** | **Recommendation engine + HITL state machine** | Turns a proven signal into the actual assistant. Explainable scoring → ranked recs; `proposed → acknowledged → placed-by-user → fill-detected → closed`. | Recs carry rationale, drivers, size/entry/stop/target; overrides captured |
| **6** | **App shell** | Makes it always-on. Tray daemon + cockpit + SQLite. Smallest thing that works. | Runs in the background, encrypted DB at rest, audit log |
| **∞** | **Autonomy (later phase)** | Only after a live-HITL track record clears the written autonomy gate, on IBKR paper first. | The charter §5 gate — not in scope now |

## Standing discipline (charter §6–8, run throughout)

- Keep a running "open decisions / assumptions" list.
- Money-touching code ships with tests + an edge-case list; red CI blocks promotion.
- Keep surfacing the **TFSA active-trading tax risk** on any turnover.
- Write-path stays disabled behind the broker-abstraction guard until the autonomy
  gate is explicitly cleared. Never quietly enable it.

## Why this order makes money rather than losing it

Steps 4–6 are the visible product, but building them first would be building a
delivery mechanism for an edge that may not exist. The charter's discipline —
and the reason it leads with capital preservation — is that **an unproven signal
wired to a live account is how retail accounts get smaller.** #1–#3 are cheap,
reversible, and answer the only question that matters first: is there a real,
after-cost, CAD edge here at all?
