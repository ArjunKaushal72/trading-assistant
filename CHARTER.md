# Project Charter — Personal Trading Research & Recommendation Assistant

> This is the founding mandate for the project. Every architecture and code
> decision must trace back to it. If a later choice conflicts with this
> document, this document wins unless it is explicitly amended here.

---

## 1. Role and Mandate

A personal trading assistant that runs as a **desktop application, always
active in the background** on the operator's own machine — not a cloud service.
It combines quantitative research, ML/AI engineering, financial engineering,
desktop/systems architecture, and trading-risk management.

The system ships in stages. **Phase 1 (now): a research-and-recommendation
assistant with a human in the loop.** It reads the operator's Questrade account
and market data, analyses signals, and surfaces ranked, confidence-scored trade
recommendations that the operator reviews and places themselves. It then detects
the resulting fills and learns from them. **Full autonomy (the app placing
orders itself) is a deliberate later phase**, unlocked only after the
human-in-the-loop version has proven itself and a broker with individual API
execution (IBKR) is wired in.

Every layer is designed so that flipping from "recommend" to "execute" later is
a **swap of one component behind an interface, not a rewrite.**

Optimize, in priority order:
**capital preservation → explainability → robustness → maintainability →
risk-adjusted return → raw return.** Never invert that order.

---

## 2. Operating Principles

1. **No hype, no hand-waving.** Every recommendation states why this over the
   alternatives, plus cost, complexity, and failure modes.
2. **Quantify the reality gap.** When an ask is unrealistic for a retail, local,
   human-in-the-loop setup, say so and give the realistic version.
3. **Smallest thing that works.** Prefer a boring, debuggable pipeline over an
   unauditable ensemble.
4. **Explainability is a hard requirement.** Every recommendation traces to
   inputs and a plain-English rationale with its top drivers.
5. **Cite live data.** Attribute every connector. Always label
   "backtested/hypothetical" vs "live."
6. **Surface and challenge assumptions** before building on them.
7. **Statistical honesty by default.** Treat every promising backtest as overfit
   until proven otherwise; hunt look-ahead, survivorship, data-snooping, regime
   dependence, and neglected costs/slippage.

---

## 3. Operator Context and Constraints

- **Operator:** single retail user, strong IT background, fluent in Python and
  GitHub, comfortable self-hosting. Not a professional quant.
- **Deployment:** personal Windows desktop app, always-on in the background
  (system tray). **Local-first** — data, models, and credentials live on the
  machine; no mandatory cloud.
- **Execution — Phase 1 (now):** Questrade read-only + human-in-the-loop. The
  app authenticates with a personal token (read-only; it cannot place orders by
  Questrade's design), syncs accounts/positions/balances/orders/executions,
  generates recommendations, and the operator places trades. The app detects the
  resulting fills via read-only endpoints and closes the learning loop.
- **Execution — later:** supervised → full autonomy via an IBKR adapter behind a
  broker-abstraction layer. Gated, earned, reversible.
- **Jurisdiction:** Canada, Manitoba.
- **Account: TFSA.** ⚠️ STANDING TAX CAVEAT: the CRA can treat frequent, active
  trading inside a TFSA as *carrying on a business*, making gains fully taxable
  as business income (and possibly penalized). This risk rises with trade
  frequency and with options. Flag it in any strategy implying meaningful
  turnover, and note when a cash or margin account would be the safer home.
- **Capital tier:** under $25k. Per-trade costs, minimum sensible position
  sizes, limited diversification, and tax friction can easily erase an edge.
  Prioritize low-turnover strategies. (The US pattern-day-trader $25k rule does
  NOT apply in Canada.)
- **Data/infra budget:** $0–50/month. Default to free tiers and local-first.
- **Asset classes:** Canadian and US equities and ETFs as the core, plus options
  where available (deferred add-on after the equity/ETF core is proven; inside a
  TFSA, options are restricted to covered calls and cash-secured puts).
- **Cadence:** minutes-to-daily. NOT HFT — no sub-second execution, no
  microstructure arbitrage.
- **Realistic target:** beat a sensible benchmark on a risk-adjusted,
  after-cost, after-tax basis without catastrophic drawdowns. Treat any pitch
  implying consistent high monthly returns as a red flag to debunk.

---

## 4. System Design Requirements

A modular, event-driven desktop application:
- Background worker/daemon (scheduled data pulls, signal runs, sync)
- System-tray presence + React cockpit UI + Python sidecar (data/ML)
- SQLite (app state, journal, config) + DuckDB (analytical/time-series)
- Data plane with **point-in-time correctness (no look-ahead)** as a first-class
  property
- Intelligence plane: LLM NLP, gradient-boosted trees (the baseline everything
  must beat), time-series models, Bayesian uncertainty; RL treated with heavy
  skepticism; prune ruthlessly
- Recommendation engine: weighted, explainable scoring → ranked recommendation
  with confidence score, plain-English rationale, top drivers, suggested
  size/entry/stop/target
- Human-in-the-loop state machine:
  **proposed → acknowledged → placed-by-user → fill-detected → closed(outcome)**,
  capturing operator overrides as training signal
- Portfolio & risk plane: advisory in Phase 1 (sizing, concentration limits,
  cash buffer, stops), enforced in code under autonomy
- Execution plane: broker-abstraction interface. Read methods live now
  (Questrade); **write methods defined but DISABLED behind a guard** requiring
  the autonomy phase + IBKR adapter
- Learning loop: journaling, attribution, regime detection, feeding outcomes
  back without leaking future information

---

## 5. Risk and Safety Framework

- **Human-in-the-loop is the primary safety property in Phase 1** — nothing
  reaches the market without the operator.
- Advisory hard limits monitored now, enforced in code under autonomy: max daily
  loss, max drawdown, per-position and per-sector caps, gross/net exposure,
  liquidity minimums.
- Pause/kill control in the UI.
- Regime/anomaly detection: withhold recommendations when out-of-distribution.
- Fail-safe defaults: on data outage, stale feed, source disagreement, or auth
  failure → stop recommending and notify. Never emit a recommendation on bad
  data.
- Autonomy gate (written down, later): paper-trading + live-HITL track record,
  confidence calibration, and drawdown criteria that must be met before any
  write method is enabled — validated first on an IBKR paper account, then
  smallest live capital, per-trade approval, graduating strategies individually.

---

## 6. Testing and Quality

Testing is a first-class deliverable. No stage advances until its suite is green
in CI. Every code delivery ships with its tests and an explicit edge-case list.

- Coverage tiered by blast radius: money/risk code gets near-exhaustive coverage;
  research/analytics gets known-answer correctness.
- The backtest engine is the highest-risk untested code — its own suite proving
  no look-ahead, no survivorship bias, correct point-in-time joins, correct
  cost/slippage on synthetic known-outcome data.
- Contract tests against Questrade read-only endpoints with recorded fixtures.
- Property-based/fuzz tests for invariants; deterministic replay; golden-file
  tests for reports.
- CI gate: tests + lint + type-check on every commit; red blocks promotion.

---

## 7. Security (desktop, local-first, Windows)

- Credentials in Windows Credential Manager — never plaintext or env files.
- Handle Questrade single-use refresh-token rotation robustly.
- Local-only: no inbound network surface; outbound calls only.
- Encrypt the local DB/journal at rest; cockpit gated behind Windows login.
- Audit log of every recommendation and every detected trade.
- Pin and scan dependencies for supply-chain risk.

---

## 8. Standing Instructions

- Name every connector used and what it returned. For each recommended
  connector: cost tier, cheaper fallback, local-vs-cloud, Phase-1-vs-later.
  Respect the $0–50/month budget; default to free tiers.
- Flag every point where ambition outruns retail reality.
- Keep surfacing the TFSA active-trading tax risk on any meaningful turnover.
- Keep the write-path (order placement) disabled behind the broker-abstraction
  guard until the autonomy gate is explicitly cleared. Never quietly enable it.
- Never deliver money-touching code without its tests and an edge-case list.
- Keep a running "open decisions / assumptions to confirm" list.
- If asked to enable autonomy early, skip the live HITL proving stage, remove a
  risk limit, or ship untested money-touching code: push back and require the
  specific risk to be acknowledged in writing first.

---

## Amendments — decisions locked during the build

These refine the charter above based on empirical findings. Newest first.

- **Data provider = Tiingo (primary), $0/month.** FMP free abandoned for research
  after it whitelisted only ~41 of 214 universe symbols (HTTP 402 on the rest).
  Stooq abandoned (returns HTML interstitials, not CSV, on this network). Tiingo
  free returns raw + adjusted prices + dividends + splits in one call/symbol;
  rate-limited to 50 requests/rolling-hour. A daily full-universe refresh
  (~4.5h) is the concrete trigger for a paid tier in Phase 8, not before.
- **Corporate-action policy.** `adjust.py` models dividends and splits only.
  SPINOFFS are not modelled (ABT/AbbVie 2013, DHR/Envista+Veralto, HLT/Park+HGV
  2017 all diverge). Therefore: **Tiingo's `adjClose` is the research series;
  our recomputation is a validation canary only.** All downstream features and
  signals read the vendor `adj_close`, never the recomputed series.
- **Phase 1 universe = US-only.** No free source covers TSX (Twelve Data free is
  US-only; FMP 402s on `.TO`). Norbert's Gambit already makes USD exposure
  workable and the pass/fail bar is exchange-agnostic, so the edge is proven or
  killed on US data at $0 first; Canada revisited only if a signal survives.
- **Cadence:** daily signals, ≤2–4 trades/month, multi-week holds (~20-day
  floor). Scale position SIZE, not frequency (tax-safe direction).
- **Benchmark:** XEQT total return, risk-adjusted, after costs, in CAD.
- **Remote access:** Tailscale (not Headscale); API bound to localhost + tailnet
  only; no inbound ports. iOS PWA, home-screen install, native Web Push.
- **Frozen Phase 1 pass/fail bar:** on the untouched 2023–2025 holdout, net of
  full costs, after survivorship haircut, in CAD — Sharpe ≥ XEQT + 0.15; max
  drawdown ≤ 1.25× XEQT; deflated Sharpe > 0 at 95%, stable under perturbation;
  ≤48 trades/year; survives 2× cost sensitivity in sign. "Much better than
  expected" is treated as a suspected bug first.
- **Congressional-trade data = conviction modifier only**, never a standalone
  signal; runs only if a base signal survives.
