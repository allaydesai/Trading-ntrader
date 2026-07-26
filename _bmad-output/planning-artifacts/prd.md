---
stepsCompleted:
  - 'step-01-init'
  - 'step-02-discovery'
  - 'step-02b-vision'
  - 'step-02c-executive-summary'
  - 'step-03-success'
  - 'step-04-journeys'
  - 'step-05-domain'
  - 'step-06-innovation'
  - 'step-07-project-type'
  - 'step-08-scoping'
  - 'step-09-functional'
  - 'step-10-nonfunctional'
  - 'step-11-polish'
  - 'step-12-complete'
inputDocuments:
  - '_bmad-output/project-context.md'
  - 'docs/product/PRD.md'
  - 'PRODUCT.md'
  - 'docs/governance/development-principles.md'
  - '_bmad-output/archive/phase-1-stocks/product-brief-Trading-ntrader.md'
  - '_bmad-output/archive/phase-2-etfs/prd.md'
  - '_bmad-output/archive/phase-1-stocks/prd.md'
  - 'docs/setup/IBKR_SETUP.md'
  - 'docs/agent/nautilus.md'
  - 'docs/agent/architecture.md'
  - 'docs/agent/persistence.md'
  - 'docs/agent/data-pipeline.md'
  - 'docs/agent/web-ui.md'
workflowType: 'prd'
documentCounts:
  briefs: 1
  research: 0
  brainstorming: 0
  projectDocs: 12
classification:
  projectType: 'CLI + Web App'
  domain: 'Fintech (algorithmic trading / live order execution)'
  complexity: 'high'
  projectContext: 'brownfield'
decisions:
  liveScope: 'Paper-only this phase; real-money live path deliberately gated'
  comparison: 'Paper sessions persist to the existing results tables and reuse the existing 2-10 run comparison UI'
  surface: 'CLI-first (CLI process owns the session); web UI read-only monitoring deferred'
  marketDataTier: 'Real-time IBKR subscription active on the paper account'
  comparisonUnit: 'Trade-count-matched samples (~100 vs ~100), not calendar-matched periods; distributional metric comparison only, no per-signal/per-trade alignment'
  sessionEnd: 'Explicit seal action, DISTINCT from stopping the process. Stopping the process must never seal and must never flatten positions'
  sessionVsRun: 'A session (logical forward test, spans weeks, seals once) is separate from a process run (start/stop, possibly daily, ephemeral). Swing positions stay open at the broker between runs'
  groundTruth: 'IBKR account state is authoritative for positions and cash. Local state is a cache. Nautilus startup + continuous reconciliation supplies this natively'
  startingCapital: 'IBKR paper account reset to the backtest starting balance ($100k default) so percentage metrics compare directly, with no normalization logic'
  sessionHours: 'RTH only, matching ibkr_use_rth=True and the FirstRate bars the backtest consumed'
  oneEngineFramework: 'HARD CONSTRAINT. Live uses Nautilus live components (TradingNode + IB data/exec clients). No second execution path, no live-specific strategy variants, no duplicate metric code. Divergent code makes the comparison uninterpretable'
  innovation: 'None claimed — screened and skipped deliberately. Forward-testing is textbook practice; backtest/live parity is inherited from Nautilus, not invented here. Contrast Phase 1, which had a real claim (no FirstRate adapter existed for NautilusTrader)'
  concurrency: 'This phase: one session, one strategy, many instruments. Framework must model a session as a LIST of strategy specs so multi-strategy needs config, not refactor'
effort: 'Phase 3 — Paper Trading (Forward-Test & Backtest Comparison)'
---

# Product Requirements Document - Trading-ntrader

**Author:** Allay
**Date:** 2026-07-26

**Effort:** Phase 3 — Paper Trading (Forward-Test & Backtest Comparison)

## Executive Summary

NTrader is a personal algorithmic trading backtester built on Nautilus Trader, with a Parquet
market-data catalog, a PostgreSQL results database, and a FastAPI/HTMX web UI. Phases 1 and 2 made
its **inputs** trustworthy: the full active US stock and ETF universe imported from FirstRate Data
across five timeframes, every instrument venue-qualified to 100% coverage, prices spot-validated
against TradingView and live FMP, and metric arithmetic hand-verified in `docs/qa/`. What no amount
of that work can establish is whether the **conclusions** hold. A backtest is a claim about the
future stated in the past tense, and its known failure modes — optimistic fills, a slippage constant
standing in for a real order book, instantaneous decisions, subtle look-ahead — are undetectable
from inside the backtest, because the backtest is the thing making the claim.

Phase 3 delivers the first independent check: **paper trading through the existing IBKR
integration, using the same strategy code that was backtested.** The operator starts a session
against an IBKR paper account on real-time market data and lets a strategy trade forward on data that
did not exist when it was designed — across days or weeks, surviving daily stops and restarts. Once
the session has accumulated a meaningful sample of trades, the operator **seals** it and reads its
metrics — win rate, profit factor, expectancy, max drawdown, Sharpe — directly alongside the
backtest's, in the existing comparison view. The delivered moment: **a sealed paper session sitting
next to its backtest in the same table, in the same units, comparable at a glance.**

The comparison is **distributional, not positional**. Paper runs forward on future data while the
backtest ran on past data, so there is no shared timeline and no meaningful per-trade alignment —
the question is whether roughly 100 paper trades reproduce the metric profile of roughly 100
backtested trades, within a range the operator judges acceptable. That judgment stays manual this
phase; the system's obligation is to persist paper results in exactly the same shape as backtest
results so the existing 2–10 run comparison works on them unchanged.

The primary (sole) user is the system operator. No multi-user, authentication, or community features
are in scope.

### What Makes This Special

The backtest and the live run are **the same code**. Same `Strategy` class, same
`order_factory`/`submit_order` calls, same `ResultsExtractor`, same tables, same UI — the only things
that change are where bars come from and where orders go. This is not an achievement of design so
much as an inheritance: Nautilus was built so backtest and live share one `Strategy` API, and
NTrader's strategies were written in pure Nautilus idiom with nothing backtest-specific leaked in.
**The port is infrastructure around code that already works.**

That property is what makes the comparison a measurement rather than a vibe check. The
industry-normal path — prototype in one tool, re-implement against the broker API in another —
renders every divergence uninterpretable: you can never separate "the market disagreed" from "my two
implementations were never the same strategy." Here that ambiguity is structurally impossible.
**When the numbers differ, strategy logic is provably not the cause.** Metric identity comes free for
the same reason: paper sessions run through the same `ResultsExtractor`, so there is no parallel
live-metrics vocabulary to invent, validate, or keep in sync.

The discipline this phase adds is **sample-size honesty**. At n≈100 trades, win rate carries roughly
±5 percentage points of sampling error at 1σ, and max drawdown — path-dependent and driven by the
single worst sequence in the sample — is noisier still. Every sealed session therefore carries its
trade count, so a difference can be read against its noise floor instead of over-read as strategy
decay.

### Scope Boundary: Paper Only, Live Gated

This phase builds the full live-trading machinery but exercises and verifies **only** the paper path.
Real-money execution is reachable by account and port alone — that is the entire difference — which
is exactly why it must be deliberately gated rather than incidentally available. `ibkr_read_only`
defaults to `True` and the setup guide recommends TWS Read-Only API mode; enabling order submission
is the feature, and it requires a designed gate, not a flag flip.

**Concurrency scope:** one session, one strategy, many instruments — mirroring how
`BacktestOrchestrator.execute_multi()` already instantiates one strategy per instrument. But a
session is modeled as a **list of strategy specs**, not one strategy plus instruments, so the
eventual multi-strategy expansion is a configuration change rather than a refactor. This phase
always passes a single-entry list.

## Project Classification

- **Type:** CLI + Web App — a long-running CLI-owned trading service; web UI monitoring deferred
- **Domain:** Fintech — algorithmic trading / live order execution
- **Complexity:** High — and a *different* high than Phases 1–2. Those were bounded, batch,
  idempotent, infinitely retryable data-correctness problems. This one is stateful and long-running
  (a `TradingNode` alive for days, in a codebase whose hardest gotchas — `LogGuard` double-init,
  single-use `BacktestEngine`, C-extension fork corruption — are all about Nautilus process
  lifecycle), has **irreversible side effects** (a submitted order cannot be un-sent; idempotent
  re-run, the safety net of both prior phases, does not exist), introduces **two new Nautilus
  surfaces at once** (live data client and live execution client, neither present in the repo
  today), and requires deliberately inverting an existing safety default
- **Context:** Brownfield — Phase 3, extending a system with two completed BMAD phases and a dozen
  shipped feature specs (001–013). Reuses the strategy registry, `ResultsExtractor`, results schema,
  comparison UI, and `IBKRSettings` unchanged

## Success Criteria

### User Success

- **Primary success moment:** Start a paper session, walk away, and come back *days later* to find it
  still running, still trading, having survived the nightly gateway restart without intervention.
  Forward-testing is a background activity, not a babysitting job.
- **Confidence moment:** Seal a session and immediately see it in the backtest list, select it plus
  the backtest it came from, and read both metric sets side by side in the existing comparison view —
  same names, same formatting, same units.
- **Resume moment:** Stop the process at the end of the day, restart it the next morning, and the
  strategy picks up mid-position — still watching for the same exit, on warm indicators, against
  positions the broker confirms it holds. The interruption leaves no trace in the trade record.
- **Trust moment:** NTrader's view of positions and cash matches what TWS shows for the same account.
  The operator never has to wonder whether the system's picture of reality is real.
- **No silent corruption:** The operator never wonders whether an order went somewhere it shouldn't.
  Real-money execution is unreachable without a deliberate, explicit act — not a config typo away.

### Business Success

- **Phase complete (checkable):** A paper session runs against an IBKR paper account on real-time
  data, completes at least one full entry→exit round trip, reconciles against IBKR's account view,
  survives a full process stop/restart cycle while holding an open position and resumes mid-position,
  and — on an explicit seal — produces a run record that the existing comparison UI reads without
  modification.
- **Programme started (ongoing, post-sign-off):** Sample accumulation begins the day the machinery
  works. The first statistically meaningful backtest-vs-paper comparison is expected *after* Phase 3
  closes and is explicitly **not** a completion criterion — trade arrival is a property of the
  strategy and the market, not of effort.
- **Reusable asset proven:** The live infrastructure is strategy-agnostic. Adding a second strategy to
  a session is a configuration change, not a code change — demonstrable by inspection of the session
  model even before multi-strategy ships.
- **Pattern repeatable:** The same `Strategy` classes, `ResultsExtractor`, results schema, and
  comparison UI serve both engines. Net-new code is confined to live infrastructure and session
  lifecycle.

### Technical Success

- **Code identity via mode-agnosticism (the load-bearing claim):** One strategy class serves both
  engines. Strategy files *may* change — two are already known to be required (indicator warm-up in
  `on_start()`; removing the `close_all_positions()` side effect from `on_stop()`) — but **every such
  change must behave identically in backtest and live.** Mode-branching inside strategy code
  (`if self.is_live:` and equivalents) is forbidden, because it destroys the property the whole phase
  rests on: that a backtest-vs-paper difference cannot be blamed on divergent code. Where live
  semantics (rejections, partial fills, post-reconnect gaps) genuinely need handling, it lives in
  shared infrastructure or a base class.
- **Metric parity by construction:** Sealed sessions run through the same `ResultsExtractor` as
  backtests. No parallel live-metrics vocabulary exists. Parity is structural, not asserted.
- **Account reconciliation (this phase's truth-test):** The system can compare its own position and
  cash state against IBKR's account view and report any discrepancy. This is the analog of Phase 1's
  row-count parity and Phase 2's FMP price validation — the external authority that makes the numbers
  trustworthy.
- **Durability across restarts (process *and* connection):** A session survives both deliberate
  process stops (routine, possibly daily) and involuntary interruptions (IB Gateway's daily
  restart/auto-logoff), resuming into the *same* record. A multi-week session is one comparable unit,
  not forty fragments. Resume is only correct when three things happen before any strategy trades:
  position state hydrated from IBKR, indicators re-warmed from historical bars, and live subscription
  started only after that history has loaded.
- **Broker-authoritative state:** IBKR's account view is the ground truth for positions and cash;
  local state is a cache that defers to it. Startup reconciliation runs before strategies start;
  continuous reconciliation keeps runtime state aligned. Nautilus supplies both natively — this is
  configuration and verification, not new machinery.
- **Stop is not seal, and stop does not flatten:** Stopping the process leaves open positions
  untouched at the broker, as a swing strategy requires. Sealing is a separate, explicit act.
  Conflating them would fragment a multi-week sample into one record per day and liquidate positions
  nightly.
- **Live gate verified:** Attempting real-money execution without the deliberate gate is refused, and
  that refusal is tested — not merely documented.
- **Nautilus process lifecycle safety:** `TradingNode` coexists with the existing `LogGuard`
  discipline without double-init. The long-running process does not inherit the single-use /
  fork-corruption hazards that govern `BacktestEngine`.
- **Session model shape:** A session is defined as a **list of strategy specs**. This phase always
  supplies exactly one; the structure does not assume it.
- **Sample size recorded:** Every sealed session carries its closed-trade count, so metric differences
  can be read against sampling noise rather than over-interpreted.

### Measurable Outcomes

- ≥1 complete round trip (entry submitted → filled → position opened → exit filled → trade persisted
  with correct P&L) before sign-off.
- Reconciliation discrepancy = **0** on positions and cash against IBKR's account view.
- Session survives ≥1 full process stop/restart **while holding an open position**, and resumes
  mid-position with warm indicators and broker-confirmed state — with no artificial exit generated by
  the stop.
- Session survives ≥1 nightly IB Gateway restart/auto-logoff and resumes into the same record without
  operator intervention.
- A sealed session produces `backtest_runs` + `performance_metrics` + `trades` rows that render
  correctly in the existing list, detail, and comparison views — zero UI changes required.
- Real-money execution attempted without the gate → refused, with a test proving it.
- Mode-branching conditionals in strategy files (`if self.is_live:` or equivalent): **0**. Strategy
  changes are permitted; mode-dependent *behavior* inside a strategy is not.

## Product Scope

### MVP — Minimum Viable Product

One indivisible vertical slice — nothing smaller is independently useful. Without execution there's
no session; without sealing there's no comparison; without durability a multi-day session can't
exist. Five capability clusters, delivered as the epics detailed in
[MVP Feature Set](#mvp-feature-set-phase-3--proposed-epic-breakdown):

1. **Live connectivity + safety gate** — IBKR live data and execution clients; real-money execution
   refused by default and reachable only by a deliberate act.
2. **Session model + lifecycle** — a session as a list of strategy specs, with identity that spans
   many process runs and `start` / `stop` / `seal` as three distinct actions.
3. **Order execution + trade capture** — full order lifecycle, duplicate-submission prevention,
   incremental trade persistence with real fills and real commission.
4. **Restart, reconciliation + resume** — broker-authoritative state, indicator warm-up, mid-position
   resume across stops and gateway restarts.
5. **Seal + comparison** — an explicit seal producing a run record through the existing
   `ResultsExtractor` and results schema, rendering in the existing views **with no UI work**.

### Growth Features (Post-MVP)

- Web UI session monitoring — live status, running equity, open positions (read-only)
- Multi-strategy sessions (config change against the MVP session model)
- Optional trade-count target with auto-seal
- Automated divergence report with a tolerance band, once real drift data exists to set one from
- Session health alerting (died, disconnected, stopped trading)

### Vision (Future)

- Real-money live trading through the gate, with risk limits and a kill switch
- Automated forward-test promotion — strategies that survive paper graduate on evidence
- Multi-account and portfolio-level paper deployment
- Divergence attribution: decomposing backtest-vs-live gap into fills, latency, spread, and data
  differences

## User Journeys

The operator is the sole user. As in Phases 1 and 2, the journeys below cover operator *modes* rather
than distinct personas: happy path, resume, verification, safety refusal, failure, and payoff.

### Session vs. Process Run (governs every journey below)

Two concepts that must not be collapsed:

| Concept | Lifetime | Behavior |
|---|---|---|
| **Process run** | Minutes to a day. Routine, possibly daily. | Start, trade, stop. Leaves open positions untouched at the broker. Never seals. |
| **Session** | Days to weeks. Spans many process runs. | The logical forward test. Accumulates the trade sample. Seals **once**, deliberately, into one comparable run record. |

Conflating them fragments a multi-week sample into one record per day and liquidates swing positions
nightly. `stop` ≠ `seal`, and `stop` never flattens.

### Journey 1: First Paper Session (Happy Path)

**Allay, solo quant trader** — has an `sma_crossover` backtest over a basket of ETFs, 2016–2024,
showing a Sharpe that looks good enough to act on. The question that won't go away: *is any of that
real?*

**Opening Scene:** The backtest is a closed world. Every fill happened at a modelled price, every
decision was instantaneous, and the slippage was a constant. Allay has no way to know which of those
assumptions is load-bearing.

**Rising Action:** Allay starts a paper session with the same strategy, same parameters, same
instruments, pointed at the IBKR paper account. The node comes up, reconciles against the account
(flat — nothing there yet), requests historical bars to warm the SMAs, then subscribes to real-time
bars. The log shows indicators initialized *before* the first live bar arrives.

**Climax:** A crossover fires. An order goes out to IBKR and comes back filled at a price nobody
modelled. A position opens. Hours later, the exit fills. **The first round trip on real
infrastructure** — persisted with actual entry, actual exit, actual commission.

**Resolution:** The session keeps running. Allay leaves it alone. The forward test has started.

> *Reveals:* CLI session start, live data client, indicator warm-up before trading, live execution
> client, order submission, fill handling, incremental trade persistence.

### Journey 2: Stop, Restart, Resume — the Signature Journey

**Opening Scene:** It's a swing strategy. Day three: two positions open, seven trades closed. Allay
wants the laptop back, and separately, IB Gateway is going to force its daily restart whether anyone
likes it or not. Under the current `on_stop()` behavior both events would liquidate the book.

**Rising Action:** Allay stops the process. **Positions stay open at IBKR** — untouched, exactly as a
swing strategy requires. Overnight the gateway restarts; nothing is running, nothing to break. Next
morning Allay starts the process again. Before any strategy runs, startup reconciliation asks IBKR
what's actually there: two open positions, at these prices, this size. That answer — **not local
state** — hydrates the cache. Indicators re-warm from historical bars. Only then do strategies start.

**Climax:** The strategy resumes *mid-position*. It knows it's long, so it's looking for an exit and
its stop level — not a fresh entry. The stop-loss it was waiting on is still the one it's waiting on.
**Nothing about the interruption is visible in the trade record.**

**Resolution:** Days pass. Process runs come and go; the session doesn't. Trade eleven lands in the
same record as trade one.

> *Reveals:* stop-without-flatten (`on_stop()` has no position side effects), positions persist at
> broker across runs, IBKR as authoritative position source, startup reconciliation, indicator
> re-warm on every start, session identity spanning many process runs, mid-position resume.

### Journey 3: Reconciliation & Trust

**Opening Scene:** Fourteen closed trades, two open. The metrics look plausible — and plausible is
exactly the state that precedes being wrong.

**Rising Action:** Allay runs the reconciliation check. The system asks IBKR for positions and cash on
the account and compares against its own view, line by line.

**Climax:** They match — or a discrepancy is named explicitly: *this instrument, this quantity, this
much cash.* No silent averaging, no "close enough."

**Resolution:** The metrics rest on state that the broker itself confirms. This is the phase's answer
to Phase 1's row-count parity and Phase 2's FMP price validation — an external authority, not
self-report.

> *Reveals:* on-demand reconciliation check, position + cash comparison against broker, explicit
> discrepancy reporting, continuous runtime reconciliation.

### Journey 4: The Live Gate

**Opening Scene:** Allay is editing `.env` and sets `IBKR_PORT=7496` — the live port — out of muscle
memory. Everything else is unchanged.

**Rising Action:** Session start. Before any client connects and before any strategy loads, the system
resolves what account it is actually about to trade and finds it isn't a paper account.

**Climax:** **Refusal.** Explicit, unambiguous, at startup. Zero orders submitted. Crossing into real
money requires a deliberate act that cannot be reached by a typo, a stale env var, or a copied
command.

**Resolution:** The one failure mode with irreversible consequences is structurally unreachable by
accident.

> *Reveals:* paper/live account detection, startup gate enforced before connection, refusal path,
> deliberate-crossing mechanism, gate covered by test.

### Journey 5: Session Death & Investigation

**Opening Scene:** Allay checks in after two days away. The process isn't running — or it is, and it
hasn't traded since Tuesday.

**Rising Action:** What happened? An order rejected for margin, or an instrument not tradeable, or a
submission outside RTH? A strategy exception? The gateway down for six hours? Allay needs the answer
without reverse-engineering it from timestamps.

**Climax:** The cause is legible — rejections logged with IBKR's reason, exceptions isolated to the
strategy that raised them rather than taking the process down silently. **And every trade that did
complete is already persisted**, because trades are written as they happen, not at the end.

**Resolution:** Allay fixes the cause and restarts into the same session, or seals it with what it
has. Nothing is lost either way.

> *Reveals:* order rejection capture with venue reason, strategy exception isolation, session
> status/health visibility, incremental (not write-at-end) persistence, resume-or-seal after failure.

### Journey 6: Sealing & the Divergence Read *(the payoff — expected post-sign-off)*

**Opening Scene:** Weeks later. 103 closed trades. Enough to mean something.

**Rising Action:** Allay seals the session — an explicit act, distinct from stopping the process. It
runs through the same `ResultsExtractor` a backtest uses and lands as a run record. Allay opens the
backtest list: **the paper session is right there among the backtests**, same table. Selects it and
the 2016–2024 backtest of the same strategy.

**Climax:** Side by side, same metric names, same formatting. Win rate 55% → 51%. Profit factor
1.60 → 1.35. Max drawdown 12% → 18%. Each carries its trade count.

**Resolution:** Allay reads it *correctly*: at n=103 the win-rate gap is inside sampling noise and
means little; the profit-factor decay is the interesting signal; the drawdown difference is
path-dependent and means less than it looks. For the first time there's **evidence about how much to
discount a backtest** — which is the entire point.

> *Reveals:* explicit seal action distinct from process stop, `ResultsExtractor` reuse, run record in
> existing schema, appears in existing list/detail/compare views unchanged, sample size carried with
> the record.

### Journey Requirements Summary

| Journey | Key Capabilities Revealed |
|---|---|
| 1 — First Paper Session | CLI session start, live data + execution clients, indicator warm-up before trading, order/fill handling, incremental trade persistence |
| 2 — Stop, Restart, Resume | Stop without flatten, broker-held positions across runs, IBKR as authoritative state, startup reconciliation, re-warm on start, session spans many process runs |
| 3 — Reconciliation & Trust | On-demand + continuous reconciliation, position/cash comparison vs broker, explicit discrepancy reporting |
| 4 — The Live Gate | Paper/live detection, startup refusal before connection, deliberate-crossing mechanism, tested |
| 5 — Session Death | Rejection capture with venue reason, exception isolation, health visibility, resume-or-seal, no data loss |
| 6 — Seal & Divergence Read | Explicit seal, `ResultsExtractor` reuse, existing schema + UI unchanged, sample size recorded |

## Domain-Specific Requirements

### Execution Correctness (the core domain risk)

Backtest execution is a two-state affair: submit, fill. Live execution is not, and the gap is where
correctness bugs live.

- **Full order lifecycle must be represented and persisted** — submitted → accepted → partially
  filled → filled / rejected / cancelled / expired. A trade record built on the assumption that
  submission implies a fill will be wrong the first time a rejection lands.
- **Partial fills aggregate into one position.** Entry and exit prices on a trade record are
  volume-weighted across fills, not the first fill's price. A backtest never exercises this path.
- **Duplicate submission is the one genuinely irreversible failure mode.** A reconnect, a
  retry-on-timeout, or a resumed session must never resubmit an order already working at the venue.
  The discipline: deterministic client order IDs, and on ambiguity **query order state rather than
  retry submission**. Retry-on-timeout for order placement is forbidden.
- **Rejections surface with the venue's reason** and are never swallowed. A strategy that silently
  stops trading because every order is being rejected for margin is the worst possible failure — it
  looks like a quiet market.
- **Buying power is real.** Position sizing math that fits on paper can still be rejected by the
  broker. Whole-share equity/ETF sizing (existing discipline) is preserved, but sizing success is no
  longer guaranteed by arithmetic.

### Comparison Integrity (what keeps backtest-vs-paper honest)

Every item here is a known divergence source. Naming them up front is what separates a measurement
from a mystery.

- **Starting capital is matched, not normalized.** The IBKR paper account is reset to the backtest's
  starting balance ($100,000 default). Percentage metrics — return %, CAGR, max drawdown %, Calmar —
  then compare directly, with no normalization logic anywhere. Both records still store their
  starting balance.
- **RTH only.** Sessions trade regular trading hours, matching `ibkr_use_rth=True` and the FirstRate
  bars the backtest consumed. This removes overnight gaps and thin extended-hours fills that have no
  backtest counterpart.
- **Price provenance differs, knowingly.** Backtest data is FirstRate: split+dividend adjusted,
  consolidated. Live is IBKR: **unadjusted**, SMART-routed. This is recorded as a property of every
  session, because it is a permanent, structural divergence source — not a defect to chase.
- **Commission moves from modelled to actual.** The backtest applies `IBKRCommissionModel`; live gets
  IBKR's real charges. The difference is not noise — it is a **free validation of the fee model**, and
  worth reading as such.
- **Slippage moves from parameter to reality.** `FillModel(prob_slippage=0.01)` plus 1bp becomes an
  actual spread against an actual book. Expected to differ; that difference is much of what this phase
  exists to measure.
- **Decision timing differs.** Backtest bars are complete on arrival; live bars close in real time and
  reach the strategy after the close. The lag is small but real and applies to every signal.
- **Sample size travels with every record**, so metric differences can be read against sampling noise
  rather than attributed to strategy decay.

### Broker-Authoritative State & Corporate Actions

- **IBKR's account view is truth for positions and cash.** Local state is a cache that defers to it on
  any conflict — never the reverse. Startup reconciliation establishes it; continuous reconciliation
  maintains it.
- **Corporate actions land live and unadjusted.** Over a multi-week swing session, a split or dividend
  will eventually change position size or cash. The backtest never saw these as events — they were
  pre-baked into adjusted prices. Broker-authoritative state absorbs them automatically; reconciliation
  makes them visible rather than silent.
- **A reconciliation discrepancy is reported, never auto-resolved by trusting local state.**

### Integration Constraints (IBKR)

- **The daily gateway restart is a certainty, not an exception**, and must be designed for rather than
  handled as an error.
- **Connection loss must never produce blind submission.** No orders while the execution client is
  disconnected; state is re-established before trading resumes.
- **Client ID allocation** must not collide with the existing historical-data client
  (`IBKR_CLIENT_ID`), which may be running concurrently.
- **Market data type is `REALTIME`**, not the `DELAYED_FROZEN` default that exists for paper data
  fetching. A live session on delayed data would give every divergence a built-in excuse.
- **Pacing discipline extends to live**: the existing 45 req/s limiter governs requests; live
  subscriptions additionally consume market data lines, which are a finite account resource.

### Security & Credential Blast Radius

- **The same credentials now authorize order submission, not just data reads.** No new secrets, but a
  materially larger blast radius for the existing ones. `.env` hygiene (never committed) is unchanged
  and non-negotiable.
- **`ibkr_read_only` must be `False` for a session to trade** — the existing safety default is
  deliberately off during operation. Containment therefore rests on the **paper/live account gate**,
  not on `read_only`. That gate is the load-bearing safety control and must be tested as such.
- Credentials and full account numbers are never logged.

### Out of Scope (Personal Tool)

KYC/AML, PCI-DSS, GDPR, SOX, regulator-facing audit trails, fraud prevention, and multi-user data
protection do not apply — single operator, no customer funds, no PII, no card data. No authentication
on the localhost CLI/UI.

**Deferred to the real-money gate (recorded, not addressed):** Pattern Day Trader restrictions (under
$25k equity, 3 day-trades per 5 days), Reg T margin, and short-sale locate/borrow. None apply to a
paper account; all apply the moment the gate is crossed.

*All risks — domain, technical, and delivery — are consolidated in a single table under
[Risk Register](#risk-register).*

## CLI + Web App Specific Requirements

### Project-Type Overview

This phase is **overwhelmingly CLI**, and unlike Phases 1–2 it introduces a process shape the codebase
has never had.

- **CLI (all of the work):** every prior CLI command is a batch job — start, run, finish, exit. A
  trading session is a **long-lived foreground process** that stays alive for a trading day, holds
  network connections, and must be stoppable and restartable without losing its identity. New
  territory.
- **Web (none this phase):** the UI participates only passively — sealed sessions appear in the
  existing list, detail, and comparison views because they are ordinary run records. **Zero UI work is
  required, and that is a success criterion, not an omission.** Live session monitoring is deferred to
  Growth.

Inherited conventions, unchanged: **MPA server-rendered HTMX** (no SPA/PWA), **single browser**
(Chrome/Safari on macOS), **no SEO**, **no authentication**, **WCAG AA** on any UI eventually added,
**typed Pydantic settings + env vars** for config, **structured log output** for CLI progress.

One convention this phase puts under future pressure: prior phases declared **no real-time /
websockets**. That holds here, but live session monitoring would be the first genuine candidate for it
when Growth lands — worth recording rather than rediscovering.

### Technical Architecture Considerations

**Process model — foreground, operator-managed.** `ntrader live start <session-id>` blocks the terminal
and streams structured logs; the operator runs it under `tmux`/`screen` if it must survive terminal
close. No daemonization, no PID files, no orphan-process ambiguity, and Ctrl-C is an honest stop. This
deliberately avoids inventing a daemon lifecycle for a single-operator tool — and it composes
correctly with the stop-and-restart-daily model, where a stopped process is the *expected* state, not
a failure.

**Session identity outlives the process** (see [Session vs. Process Run](#session-vs-process-run-governs-every-journey-below)
for the conceptual distinction). Its lifecycle:

```
CREATED ──start──▶ RUNNING ──stop/Ctrl-C──▶ STOPPED ──start──▶ RUNNING …
                                                │
                                              seal
                                                ▼
                                             SEALED  (terminal)
```

`STOPPED` is a normal resting state, not an error. `SEALED` is terminal and produces the comparable run
record. Sealing requires the session to be stopped — never sealing a live process out from under
itself.

**Immutable session spec.** The full spec (strategy list, parameters, instruments, linked backtest
reference, starting capital, RTH policy, data provenance) is captured at creation and stored, then
reloaded by session ID on every subsequent start. It is **immutable for the session's life** — the same
discipline `config_snapshot` already enforces on `backtest_runs`. Re-specifying parameters at each
start would let a typo silently change the strategy mid-forward-test, corrupting a multi-week sample in
a way no downstream check could detect.

**State ownership splits cleanly:**

| State | Home | Rationale |
|---|---|---|
| Nautilus engine state (orders, positions, cache) | **Redis** | Framework-native cache database; `redis:7-alpine` already provisioned in `docker-compose.yml` |
| Session metadata (ID, spec, status, linked backtest, timestamps) | **PostgreSQL** | Must become a `backtest_runs` row on seal; belongs with the results spine |
| Ground truth for positions and cash | **IBKR** | Both stores above defer to the broker on conflict |

**Client ID allocation.** The live session's IBKR client ID must not collide with the historical-data
client (`IBKR_CLIENT_ID`), which may be running concurrently for catalog fetches.

**`LogGuard` coexistence.** `TradingNode` initializes Nautilus logging. The existing
`set_nautilus_log_guard()` / `_guard_nautilus_logging()` discipline must cover it — the same C-logging
double-init panic that governs `BacktestEngine` and `IBKRHistoricalClient` applies.

### Command Structure

Click command group `ntrader live`, following existing CLI structure:

| Command | Purpose |
|---|---|
| `create` | Define a session — strategies, instruments, params, linked backtest. Persists the immutable spec, returns a session ID. |
| `start <session-id>` | Foreground run. Gate check → reconcile against IBKR → warm indicators → subscribe → trade until stopped. |
| stop (Ctrl-C on the foreground process) | Graceful shutdown. **Leaves positions open at the broker.** Never seals. |
| `status [<session-id>]` | Session state, trade count, open positions, last activity, health. |
| `list` | All sessions with state and trade counts. |
| `reconcile <session-id>` | On-demand positions + cash comparison against IBKR; reports discrepancies explicitly. |
| `seal <session-id>` | Explicit, terminal. Produces the comparable run record via `ResultsExtractor`. |

### Configuration Schema

- **Connection and credentials:** existing `IBKRSettings` — no new settings class needed.
  `ibkr_market_data_type` set to `REALTIME` (not the `DELAYED_FROZEN` default), `ibkr_read_only`
  `False` for trading sessions, `ibkr_use_rth` `True`.
- **Session spec:** persisted per session, not env-driven — it varies per experiment and must be
  immutable once created.
- **Strategy parameters:** resolved through the existing `StrategyFactory.build_strategy_params()`
  chain (overrides → settings map → Pydantic defaults), then frozen into the session spec.
- **Credentials never in the session spec** — connection settings stay in `IBKRSettings`/`.env`; the
  spec references *what* to trade, never *how to authenticate*.

### Output Formats & Scripting Support

- **Streaming:** structured `structlog` console output while running — bars processed, signals, orders
  submitted, fills, rejections with venue reason, reconnects.
- **`status` / `list`:** human-readable tables (Rich, matching existing CLI) with a `--json` option per
  the CLI standards in `docs/governance/development-principles.md`.
- **Exit codes:** 0 success, 1 general error, 2+ specific — notably a distinct code for **gate
  refusal**, so a script can tell "refused to trade a live account" apart from "failed to connect."
- **Non-interactive:** every command scriptable; no prompts on the running path. The one exception is
  the deliberate real-money crossing, which *should* be hard to automate.
- **No shell completion** — consistent with Phases 1–2.

### Implementation Considerations

- **Reuse over rebuild:** `StrategyRegistry`/`StrategyFactory` for strategy resolution,
  `ResultsExtractor` for metrics, existing `backtest_runs`/`performance_metrics`/`trades` schema for
  persistence, existing sync CLI repository path (`session_sync.py`) for writes.
- **Dual-repository rule applies:** per project convention, DB features must land in both async (web)
  and sync (CLI) repositories, even though only the CLI writes this phase.
- **Incremental persistence:** trades are written as they close, never batched to session end —
  Journey 5 depends on nothing being lost when a process dies.
- **Nautilus reconciliation is configured, not built:** `reconciliation=True` for startup, plus
  continuous reconciliation settings. The work is configuration, verification, and testing — not new
  machinery.
- **Migration required:** session metadata and the paper/backtest distinction need schema changes — a
  5th Alembic migration.
- **Open decision for the architecture phase:** what happens to **open positions at seal time**.
  Leaving them orphans positions in the paper account that no session owns; flattening manufactures
  artificial exits in the final trades. The PRD requires only that the behavior be **explicit and
  reported**, never silent — the policy choice belongs to architecture.

## Project Scoping & Phased Development

### Architectural Constraint: One Engine Framework, Not Two

**This is the constraint the entire phase rests on.** Live trading uses Nautilus's own live components
(`TradingNode` + IBKR data/execution clients); it does **not** introduce a second execution path, a
live-specific strategy variant, or duplicate metric code. Any live-specific logic that cannot be
expressed through Nautilus's own abstractions is a design smell and must be escalated, not worked
around — because the moment the two paths diverge in code, every backtest-vs-paper comparison becomes
uninterpretable.

Nautilus is explicitly designed for this: *"deployment of backtested strategies to live markets without
requiring code modifications — the same actors, strategies, and execution algorithms can be utilized
for both."* The sharing boundary:

| Layer | Backtest | Live | Status |
|---|---|---|---|
| Strategy class (`on_bar`, `order_factory`, `submit_order`, `cache`, `portfolio`) | — | — | **Identical** |
| Nautilus core (MessageBus, Cache, Portfolio, RiskEngine, ExecutionEngine, PortfolioAnalyzer) | — | — | **Identical** |
| Host container | `BacktestEngine` | `TradingNode` | Swapped |
| Clock | `TestClock` | `LiveClock` | Swapped |
| Data client | Parquet catalog | IBKR real-time stream | Swapped |
| Execution | `SimulatedExchange` + `FillModel` + `IBKRCommissionModel` | IBKR execution client — real fills, real commission | Swapped |

The swapped bottom layer is precisely what the phase exists to measure. Everything that could confound
that measurement stays identical.

**Consequences for NTrader's own code:**

- `BacktestOrchestrator` is backtest-specific and gains a **sibling** (the session runner), not a fork.
  Both call the same `StrategyRegistry` and `StrategyFactory.build_strategy_params()`.
- `ResultsExtractor` currently takes a `BacktestEngine`; a live node exposes the same `portfolio` and
  `cache` components, so it needs a thin widening to accept either host. **The metric math is
  untouched.** (E5 task.)
- `FillModel` / `IBKRCommissionModel` have no live counterpart — IBKR fills and charges for real. Not a
  gap; the experiment.
- **Architecture-phase version check:** current Nautilus docs show both a `TradingNode` and a newer
  `LiveNode` builder API. Which applies depends on the pinned `nautilus-trader >=1.190.0` version.

### MVP Strategy & Philosophy

**MVP Approach:** Problem-solving MVP. The gating problem is that no independent check on the
backtester exists. The whole of Phase 3 is one indivisible vertical slice — nothing smaller is useful,
because without execution there's no session, without resume a swing strategy can't run, and without
sealing there's nothing to compare.

**Resource model:** Solo developer. Epics are sequenced so each lands a verifiable increment, and
**backtesting remains fully functional throughout** — this phase adds a surface, it never modifies the
backtest path. Pause-and-resume safe at any epic boundary.

**Two sequencing principles govern the epic order:**

1. **Riskiest assumption first.** The repo's entire IBKR footprint today is historical data via
   `IBKRHistoricalClient`; the live data and execution client paths are unproven here. If that adapter
   has gaps, everything downstream is affected — so it is tested in E1, before anything is built on it.
2. **Safety before capability.** The paper/live gate ships in E1, *before* any code path exists that
   can submit an order. Building execution first and adding the gate later would create a window in
   which accidental live trading is reachable.

### MVP Feature Set (Phase 3 — proposed epic breakdown)

**Core User Journeys Supported:** all six.

- **E1 — Live Connectivity & Safety Gate** *(foundation; retires the biggest technical risk first)*
  IBKR live data + live execution clients wired into a `TradingNode`; `LogGuard` coexistence; client-ID
  allocation that cannot collide with the historical-data client; `REALTIME` market data; RTH policy.
  **Paper/live account detection and the startup gate — enforced before any client connects, and
  tested.**
  *Deliverable:* connects to IBKR paper, receives real-time bars, and **refuses** a live account. No
  orders exist yet.

- **E2 — Session Model & Lifecycle**
  Session entity with immutable spec; `CREATED → RUNNING → STOPPED → SEALED` state machine;
  `create`/`start`/`stop`/`status`/`list` commands; Alembic migration for session metadata; foreground
  process with streaming structured logs; Redis cache config for engine state.
  *Deliverable:* a named session runs, observes bars, stops, and restarts under the same identity.
  Still no orders.

- **E3 — Execution & Trade Persistence** *(Journey 1)*
  Live order submission through the existing strategy code path; full order lifecycle (accepted /
  partial / filled / rejected / cancelled); **duplicate-submission prevention** via deterministic
  client order IDs and query-don't-retry; volume-weighted entry/exit across partial fills; rejection
  capture with venue reason; incremental trade persistence. Strategy lifecycle correction — remove
  position side effects from `on_stop()`.
  *Deliverable:* **the first complete round trip**, persisted with real fills and real commission.

- **E4 — Restart, Reconciliation & Resume** *(Journey 2 — the signature epic)*
  Startup reconciliation configured and verified; continuous reconciliation; indicator warm-up
  (`register_indicator_for_bars` → `request_bars(callback=subscribe_bars)`); mid-position resume across
  both deliberate process stops and gateway restarts; `reconcile` command reporting discrepancies
  explicitly.
  *Deliverable:* swing strategies become viable — stop at night, resume next morning mid-position, no
  trace in the trade record.

- **E5 — Seal & Comparison** *(Journey 6)*
  `seal` command → `ResultsExtractor` (widened to accept a live host) → run record; sample size
  recorded; starting-capital match verified; open-position-at-seal policy made explicit; sealed
  sessions render in the existing list, detail, and comparison views.
  *Deliverable:* a paper session sits next to its backtest, comparable, **with zero UI changes**.

### Post-MVP Features

Growth and Vision items are enumerated in [Product Scope](#product-scope). The Phase-3-specific note:
**E2's session model is deliberately built as a list of strategy specs**, so multi-strategy sessions
later are a configuration change rather than a refactor — the same "build the general thing once"
discipline that made Phase 2's E1 metadata loader reusable.

### Risk Register

Consolidated across domain, technical, and delivery risk. Ordered by consequence: irreversible harm
first, then correctness, then delivery.

| Risk | Type | Mitigation | Owner |
|---|---|---|---|
| Accidental real-money execution | **Irreversible** | Paper/live gate enforced at startup before any client connects; ships *before* any order-submitting code exists; refusal is tested, not just documented | E1 |
| Duplicate order on reconnect, retry, or resume | **Irreversible** | Deterministic client order IDs; query order state rather than retry on ambiguity; startup reconciliation; explicitly tested. Not left to "we'll be careful" | E3 |
| A second execution path emerges under schedule pressure | **Fatal to the phase's purpose** | "One engine framework" is a stated architectural constraint; live-specific logic that cannot route through Nautilus abstractions is escalated, not worked around | All |
| Nautilus IB live adapter has gaps for our instruments | Technical (highest unknown) | Sequenced first — E1 proves connectivity and execution plumbing before anything is built on it. Failure surfaces in week one, not month two | E1 |
| Long-running process destabilizes on Nautilus C extensions | Technical | `LogGuard` coexistence proven in E1; `TradingNode` lifecycle isolated from `BacktestEngine`'s single-use and fork-corruption constraints | E1 |
| Local state drifts from broker | Correctness — every downstream metric becomes fiction | IBKR authoritative; startup + continuous reconciliation; on-demand check reports discrepancies explicitly, never auto-resolves toward local state | E4 |
| `on_stop()` flattening on every daily restart | Correctness — fake round trips, destroyed swing behavior | Remove position side effects from `on_stop()`; flattening becomes a deployment decision, not a strategy behavior | E3 |
| Cold indicators after restart | Correctness — garbage signals, or a week of silence | `register_indicator_for_bars()` → `request_bars(callback=subscribe_bars)`; live stream gated on history having loaded | E4 |
| Capital mismatch ($1M paper default vs $100k backtest) | Comparison integrity — percentage metrics diverge by arithmetic alone | Paper account reset to the backtest's starting balance; both records store their own | E5 |
| Adjusted-vs-unadjusted, consolidated-vs-SMART price divergence | Comparison integrity — unexplained gaps | Recorded as a session property up front; expected and attributable, not chased as a defect | E5 |
| Rejections silently stop trading | Operational — looks identical to a quiet market | Rejections logged with venue reason; session continues; health surfaced via status | E3 |
| Session dies unnoticed over days | Operational — lost calendar time on an already slow sample | Incremental persistence means nothing is lost; status/health visibility; alerting deferred to Growth | E2 |
| Scope creep into web UI monitoring | Resource | Explicitly Growth. Zero-UI-change is a *success criterion* for E5, making creep a criterion violation rather than a judgment call | E5 |
| Scope creep into multi-strategy | Resource | Session model supports it structurally; shipping it is deferred. The list-of-specs design means deferring costs nothing later | E2 |
| Calendar-time trap — waiting on a trade sample to sign off | Schedule | Phase gate is machinery-complete (≥1 round trip), never sample-complete. Sample accumulation is post-sign-off operations | — |
| "Ship it, harden later" applied to irreversible operations | Process | Duplicate prevention and the gate are MVP scope, not hardening. Everything reversible can be deferred; nothing irreversible can | — |
| Solo developer — effort stalls | Resource | Five independently verifiable epics; backtesting untouched and fully functional throughout; resume at any boundary | — |

*(Market risk: N/A — personal single-operator tool.)*

## Functional Requirements

### Live Connectivity & Market Data (E1)

- **FR1:** System can connect to Interactive Brokers as a live data source and receive real-time bars
  for subscribed instruments.
- **FR2:** System can connect to Interactive Brokers as an execution destination capable of submitting
  and tracking orders.
- **FR3:** System can operate on real-time market data rather than delayed data.
- **FR4:** System can restrict trading activity to regular trading hours, matching the session basis of
  the backtest data.
- **FR5:** System can maintain live broker connections without conflicting with a historical-data
  client operating concurrently.
- **FR6:** System can detect loss of broker connectivity and refrain from submitting orders while
  disconnected.
- **FR7:** System can run a live trading process alongside the existing backtest and data-fetch
  components without trading-framework initialization conflicts.

### Trading Safety & Account Gating (E1)

- **FR8:** System can determine whether the configured broker connection targets a paper account or a
  real-money account.
- **FR9:** System can refuse to start a session against a real-money account, before establishing any
  connection and before any order can be submitted.
- **FR10:** Operator can deliberately authorize real-money trading through an explicit mechanism that
  cannot be triggered by configuration error alone.
- **FR11:** System can report a safety refusal distinguishably from other startup failures.
- **FR12:** System can enable order submission for sessions permitted to trade, while defaulting to
  non-trading operation.

### Session Definition & Lifecycle (E2)

- **FR13:** Operator can define a trading session comprising one or more strategy specifications, each
  with its own parameters and target instruments.
- **FR14:** System can persist a session's specification immutably at creation, so it cannot change
  over the session's life.
- **FR15:** Operator can associate a session with the backtest it is intended to be compared against.
- **FR16:** Operator can start a session by reference to its identifier, without re-specifying its
  parameters.
- **FR17:** Operator can stop a running session without ending it, leaving it resumable.
- **FR18:** System can stop a session without closing or modifying open positions.
- **FR19:** System can preserve session identity across any number of stop/start cycles.
- **FR20:** System can represent session state distinctly across created, running, stopped, and sealed
  conditions.
- **FR21:** Operator can list all sessions with their current state and accumulated trade count.
- **FR22:** Operator can view an individual session's state, trade count, open positions, and last
  activity.

### Order Execution & Trade Capture (E3)

- **FR23:** System can submit orders generated by strategy logic to the broker.
- **FR24:** System can track the full lifecycle of a submitted order — acceptance, partial fill,
  complete fill, rejection, cancellation, and expiry.
- **FR25:** System can aggregate multiple partial fills into a single position using volume-weighted
  prices.
- **FR26:** System can capture order rejections together with the reason reported by the broker.
- **FR27:** System can prevent submission of a duplicate order for a request already working at the
  broker.
- **FR28:** System can persist each completed trade at the time it closes, rather than deferring
  persistence to session end.
- **FR29:** System can record broker-charged commissions on each trade as actually incurred.
- **FR30:** System can apply whole-share position sizing for equity and ETF instruments.
- **FR31:** System can continue operating when an order is rejected, without silently ceasing to trade.

### State Reconciliation & Resume (E4)

- **FR32:** System can retrieve the broker's authoritative view of positions and cash for the
  configured account.
- **FR33:** System can reconcile internal order and position state against the broker's state at
  session start, before any strategy begins trading.
- **FR34:** System can maintain alignment between internal state and broker state throughout a running
  session.
- **FR35:** System can resolve any conflict between internal state and broker state in favor of the
  broker.
- **FR36:** Operator can request an on-demand reconciliation check and receive an explicit report of
  any discrepancy found.
- **FR37:** System can restore strategy indicator state from historical data before that strategy
  begins processing live data.
- **FR38:** System can resume a strategy mid-position, such that it continues seeking exits and stop
  levels for positions opened during a prior process run.
- **FR39:** System can absorb position and cash changes arising from corporate actions via
  broker-authoritative state.

### Session Sealing & Comparison (E5)

- **FR40:** Operator can seal a stopped session, ending it permanently and producing a results record.
- **FR41:** System can compute a sealed session's performance metrics using the same calculation path
  used for backtests.
- **FR42:** System can persist a sealed session's results in the same form as backtest results, without
  a parallel results structure.
- **FR43:** System can record the closed-trade count with a sealed session's results.
- **FR44:** System can distinguish a paper-trading record from a backtest record.
- **FR45:** System can record a session's execution conditions with its results — starting capital,
  trading-hours policy, price-adjustment basis, and market data tier.
- **FR46:** Operator can view a sealed session in the existing results list, detail, and comparison
  views alongside backtests.
- **FR47:** System can handle open positions at seal time explicitly and report their disposition.

### Observability & Diagnostics (cross-cutting)

- **FR48:** System can report session activity as structured streaming output during execution — bars
  processed, signals, orders, fills, rejections, and reconnections.
- **FR49:** Operator can determine why a session stopped trading or terminated.
- **FR50:** System can isolate a failure in one strategy from other strategies within the same session.
- **FR51:** Operator can obtain machine-readable output for session status and listing.

### Configuration (cross-cutting)

- **FR52:** Operator can supply broker connection settings and credentials via typed environment-based
  configuration, never in the session specification.
- **FR53:** Operator can configure which instruments and strategy parameters a session trades at
  creation time.

### Coverage & Deliberate Exclusions

**Journey coverage:** J1 → FR1–7, 23–30 · J2 → FR17–19, 32–38 · J3 → FR32–36 · J4 → FR8–12 ·
J5 → FR26, 28, 31, 48–50 · J6 → FR40–47.

**Deliberately absent (Growth scope, recorded so their absence is intentional rather than an
oversight):** trade-count auto-seal, divergence tolerance bands, live session monitoring UI,
multi-strategy execution, session health alerting.

**FR13's "one or more strategy specifications" is the structural hook** that makes multi-strategy a
configuration change later — the capability contract permits it even though this phase always supplies
exactly one.

## Non-Functional Requirements

Deliberately selective. **Scalability** is omitted — one operator, one session, no growth curve to plan
for. **Accessibility** is omitted for this phase — no new UI ships; the inherited WCAG AA commitment
from `PRODUCT.md` applies when monitoring lands in Growth.

**Reliability is the dominant category**, inverting the prior two phases. Import pipelines could fail
and be re-run; that safety net does not exist here.

### Performance & Latency

| Operation | Target | Rationale |
|---|---|---|
| Bar close → order submission | < 1 second | The backtest submits instantly; live latency is a divergence source. The real requirement is that it stays small relative to the shortest traded timeframe **and is observable**, not that it hits a specific number |
| Bar processing throughput | No accumulating backlog | Processing for one bar completes well before the next arrives, at the shortest supported timeframe. Unbounded queue growth means the strategy is trading stale data |
| Session startup (connect → reconcile → warm indicators → subscribe) | **A session started 5 minutes before the open is trading at the open** | The operationally meaningful form. Bounded by IBKR historical-data pacing, so stated as an outcome rather than a raw duration |
| Reconnection after transient disconnect | < 60 seconds | Missed bars during a gap are unrecoverable trading opportunities |
| Reconciliation check | < 30 seconds | Runs at every start and on demand; slow enough to skip is slow enough to be skipped |

No target on sealing or session listing — infrequent, operator-initiated, not on any critical path.

### Reliability

- **Duplicate orders: exactly 0.** Not a target — an invariant. No reconnect, retry, or resume path may
  resubmit a working order.
- **Unattended uptime:** a session sustains a full RTH trading day (6.5 hours) without operator
  intervention.
- **Zero trade loss on abrupt termination:** after a hard kill (`SIGKILL`, power loss), every trade that
  had closed is present in the database. Verified by killing a running session and comparing.
- **Resume correctness:** after any restart, internal position state matches the broker's exactly —
  **0 discrepancy** — before any strategy is permitted to trade.
- **No blind trading:** zero orders submitted while the execution client is disconnected or while
  reconciliation is incomplete.
- **Always-valid session state:** an interrupted session is always in a state that can be started or
  sealed. No state exists from which a session is stuck.
- **Failure isolation:** a failure in one strategy does not terminate the session or affect other
  strategies within it.
- **Rejections never silence a session:** an order rejection is logged with its venue reason and the
  session continues operating.
- **No artificial trades:** no stop, restart, disconnect, or reconnect generates an entry or exit the
  strategy did not request.

### Integration (IBKR — the entire external surface)

- **Rate-limit compliance:** the existing 45 req/s discipline governs live requests; historical warm-up
  requests respect IBKR's pacing rules.
- **Market data line budget:** a session's instrument count stays within the account's concurrent
  subscription limit. Exceeding it fails at startup with a clear message rather than silently dropping
  subscriptions.
- **Client ID isolation:** the live session never contends with the historical-data client for a client
  ID.
- **Reconciliation is mandatory, not optional:** every session start reconciles against the broker.
  There is no path that starts trading on unverified state.
- **Gateway restart tolerance:** the daily restart/auto-logoff is handled as an expected event, not an
  error condition.
- **Safe degradation:** when IBKR is unreachable, the session halts trading safely and reports it. It
  never estimates, assumes, or proceeds on stale state.

### Observability

- **Every order is traceable end to end** — submission, acknowledgement, fills, final state, and venue
  reason on rejection. This is not compliance theatre: the order history is the evidence the entire
  backtest-vs-paper comparison rests on.
- **Session-scoped correlation:** all structured log output carries the session identifier, extending
  the project's existing correlation-ID convention.
- **Status without log archaeology:** current session state, trade count, open positions, and last
  activity are answerable from a status query alone.
- **Silence is distinguishable from death:** a session that is running but not trading is reported
  differently from one that has stopped.

### Security

- **Credential hygiene:** connection settings and credentials exclusively via typed environment
  configuration; never in a session specification, never committed, never logged.
- **Account identifiers are not logged in full.**
- **Elevated blast radius acknowledged:** the same credentials that previously permitted only data reads
  now authorize order submission. No new secrets are introduced, but `ibkr_read_only=False` is only ever
  set for a session that has passed the paper/live gate — the gate, not `read_only`, is the load-bearing
  control.
- **No authentication or authorization** on the localhost CLI — consistent with Phases 1–2, personal
  single-operator tool.

### Capacity (in place of Scalability)

- **One concurrent session** this phase. Multi-session is Growth and is a deliberate non-goal.
- **Instrument count within one session** is bounded by the account's market data lines, not by system
  design.
- **No horizontal scaling, no multi-node, no distributed state.** The Redis cache serves persistence,
  not distribution.

### Testability (elevated to an NFR — live trading resists conventional testing)

- **Automated tests never touch a real broker.** Session lifecycle, order-state handling, duplicate
  prevention, and reconciliation logic are tested against broker doubles across the existing unit /
  component / integration (`--forked`) tiers.
- **Broker-dependent behavior is verified by operator-run procedures against the real paper account** —
  connectivity, the safety gate refusal, a complete round trip, restart resume, and reconciliation.
  These are the phase-gate evidence and cannot live in CI.
- **The safety gate has an automated test**, since it must hold regardless of environment.

### Maintainability (project standard)

Conforms to existing standards: Python 3.11+ with type hints, `ruff` + `mypy` gates, file/function/class
size limits, TDD with the established test pyramid (unit / component / integration `--forked` / e2e),
>80% coverage on `src/core` + `src/strategies`.
