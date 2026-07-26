---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-07-26'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/project-context.md'
  - 'docs/agent/architecture.md'
  - 'docs/agent/conventions.md'
  - 'docs/agent/data-pipeline.md'
  - 'docs/agent/nautilus.md'
  - 'docs/agent/persistence.md'
  - 'docs/agent/testing.md'
  - 'docs/agent/web-ui.md'
workflowType: 'architecture'
project_name: 'Trading-ntrader'
user_name: 'Allay'
date: '2026-07-26'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**

53 FRs in 7 clusters, mapping cleanly onto 5 proposed epics (E1–E5). Architecturally they decompose into:

- **Live connectivity (FR1–7):** first use of Nautilus live components (`TradingNode`, IB data + execution
  clients) in this repo. Must coexist with `IBKRHistoricalClient` (client-ID isolation, LogGuard
  discipline) and run alongside backtesting without initialization conflicts. REALTIME market data,
  RTH-only, no blind submission while disconnected.
- **Safety gate (FR8–12):** paper/live account detection enforced at startup *before any connection*;
  refusal is a distinct, testable, scriptable outcome (dedicated exit code). The gate — not
  `ibkr_read_only` — is the load-bearing control, since `read_only` must be False to trade.
- **Session model (FR13–22):** new domain entity. A session is a *list of strategy specs* (multi-strategy
  is a config change later, never a refactor), with an immutable spec captured at creation
  (`config_snapshot` discipline extended), identity spanning unlimited process runs, and a
  `CREATED → RUNNING ⇄ STOPPED → SEALED` state machine. Stop never flattens; stop never seals.
- **Execution & capture (FR23–31):** full order lifecycle representation (accepted / partial / filled /
  rejected / cancelled / expired), volume-weighted aggregation of partial fills, duplicate-submission
  prevention via deterministic client order IDs + query-don't-retry, rejection capture with venue
  reason, incremental trade persistence (write-on-close, never batch-at-end).
- **Reconciliation & resume (FR32–39):** IBKR account state is authoritative; local state is a cache.
  Startup reconciliation → indicator warm-up from historical bars → only then live subscription.
  Mid-position resume with warm indicators. Largely Nautilus-native configuration, but verification
  and sequencing are ours.
- **Seal & comparison (FR40–47):** seal is an explicit terminal act on a stopped session, producing a
  run record through the *same* `ResultsExtractor` and results schema as backtests (widened to accept
  a live host). Paper records distinguishable from backtests; execution conditions and closed-trade
  count recorded; renders in existing list/detail/compare views with zero UI changes.
- **Observability & config (FR48–53):** structured streaming logs with session-scoped correlation IDs,
  status/list with `--json`, failure isolation per strategy, credentials only via `IBKRSettings`/env —
  never in the session spec.

**Non-Functional Requirements:**

Reliability dominates (inverting Phases 1–2):

- **Invariants, not targets:** duplicate orders = 0; resume-state discrepancy vs broker = 0 before any
  trading; zero trade loss on SIGKILL; no artificial entries/exits from stop/restart/reconnect; no
  orders while disconnected or unreconciled; sessions never enter a stuck state.
- **Performance:** bar-close → order submission < 1s and observable; no bar-processing backlog; a
  session started 5 minutes before the open trades at the open; reconnect < 60s; reconciliation < 30s.
- **Integration (IBKR is the entire external surface):** 45 req/s pacing, market-data line budget
  checked at startup, client-ID isolation, daily gateway restart designed for as a normal event, safe
  degradation when unreachable.
- **Testability (elevated to NFR):** automated tests never touch a real broker — broker doubles across
  unit/component/integration (`--forked`) tiers; broker-dependent behavior verified by operator-run
  procedures against the paper account; the safety gate has an automated test regardless.
- **Security:** larger blast radius for existing credentials (orders, not just reads); credentials and
  full account numbers never logged; no new secrets.
- **Deliberately omitted:** scalability (one operator, one session) and accessibility (zero UI ships).

**Scale & Complexity:**

- Primary domain: CLI-owned long-running trading service extending a brownfield Python 3.11 / Nautilus /
  FastAPI / PostgreSQL+TimescaleDB / Redis system
- Complexity level: high — stateful, long-lived, irreversible side effects, two new Nautilus surfaces
  (live data + live execution) introduced at once
- Estimated architectural components: ~8 net-new (session entity + repository pair, session runner
  sibling to `BacktestOrchestrator`, live node assembly/config, safety gate, reconciliation verifier,
  seal service, `ntrader live` CLI group, new Alembic migration) plus 2 targeted modifications
  (`ResultsExtractor` host widening; strategy lifecycle corrections: `on_stop()` flatten removal,
  `on_start()` indicator warm-up)

### Technical Constraints & Dependencies

- **One engine framework (hard constraint):** live = Nautilus `TradingNode` + IB adapters. No second
  execution path, no live strategy variants, no duplicate metric code. Mode-branching in strategies
  (`if self.is_live:`) is forbidden; live-specific semantics live in shared infrastructure or a base
  class. Anything that can't route through Nautilus abstractions is escalated, not worked around.
- **Version check owed by this workflow:** `TradingNode` vs newer `LiveNode` builder API for the pinned
  `nautilus-trader >=1.190.0`.
- **Nautilus process lifecycle:** LogGuard single-init discipline must cover `TradingNode`
  (`set_nautilus_log_guard()` / `_guard_nautilus_logging()`); `BacktestEngine` single-use and
  fork-corruption rules must not leak into the long-running process design.
- **State ownership (PRD-decided):** Redis = Nautilus engine/cache state (`redis:7-alpine` already
  provisioned); PostgreSQL = session metadata joining the results spine; IBKR = ground truth both defer to.
- **Existing assets reused unchanged:** `StrategyRegistry`/`StrategyFactory.build_strategy_params()`,
  `ResultsExtractor` (thin widening only), `backtest_runs`/`performance_metrics`/`trades` schema,
  comparison UI, `IBKRSettings`, sync CLI repository path, structlog conventions.
- **Project conventions that bind:** dual async/sync repository rule; TDD with the established pyramid;
  UV-only; Decimal for money; typed Pydantic settings; size limits; new Alembic migration required
  (note: the repo has 14 migration versions, not the 4 the PRD/docs cite — that doc note is stale).
- **Comparison-integrity constants:** starting capital matched ($100k, reset paper account, no
  normalization); RTH only (`ibkr_use_rth=True`); price provenance divergence (FirstRate adjusted vs
  IBKR unadjusted SMART) recorded as a session property; real commission replaces `IBKRCommissionModel`.
- **PRD-flagged open decision owned by architecture:** disposition of open positions at seal time —
  must be explicit and reported, never silent.

### Cross-Cutting Concerns Identified

1. **Safety gating** — paper/live detection before connection, deliberate real-money crossing
   mechanism, distinct exit code, automated test. Ships before any order-submitting code exists.
2. **Idempotency & duplicate prevention** — deterministic client order IDs; on ambiguity, query order
   state, never retry submission. Spans execution, reconnect, restart, and resume paths.
3. **Broker-authoritative state** — startup + continuous reconciliation; discrepancies reported, never
   auto-resolved toward local state; absorbs corporate actions on multi-week sessions.
4. **Process-lifecycle coexistence** — LogGuard, client-ID allocation vs the historical-data client,
   graceful Ctrl-C, honest foreground process (no daemon).
5. **Session-scoped observability** — correlation-ID convention extended to session IDs; every order
   traceable end-to-end; "running but not trading" distinguishable from "dead."
6. **Incremental durability** — trades persisted as they close; session state always resumable or
   sealable after any interruption.
7. **Mode-agnostic strategy discipline** — strategy changes allowed (warm-up, on_stop fix) but must
   behave identically in both engines; enforcement is a review/test concern touching every strategy file.
8. **Dual-repository rule** — session tables need async + sync repositories even though only the CLI
   writes this phase.

## Starter Template Evaluation

### Primary Technology Domain

Brownfield CLI-owned live-trading service — Phase 3 of an existing system. No starter template
applies; the existing NTrader codebase is the foundation, and its stack is settled
(Python 3.11+, nautilus-trader[ib], FastAPI/HTMX, PostgreSQL 16 + TimescaleDB, Redis, SQLAlchemy 2
async+sync, Click, UV, structlog, pytest tiers).

### Starter Options Considered

None — evaluating starters would be architecture theatre for a phase whose hard constraint is
maximal reuse of the existing system (`StrategyRegistry`, `StrategyFactory`, `ResultsExtractor`,
results schema, comparison UI, `IBKRSettings`). The relevant "foundation decision" for this phase
is which Nautilus live API generation to build on, which the PRD explicitly assigned to this
workflow. Verified against the installed package rather than docs:

### Foundation: Existing Codebase + Nautilus `TradingNode` (v1.220.0)

**Version check (PRD architecture-phase action item — RESOLVED):**

- Pinned: `nautilus-trader[ib]>=1.190.0`; locked and installed: **1.220.0**
- **`TradingNode` is the live host API at this version.** `LiveNode` does not exist in 1.220.0
  (`nautilus_trader.live.node` exposes only `TradingNode`); the newer `LiveNode` builder API seen
  in current Nautilus docs belongs to a later, unpinned generation. Decision: build on
  `TradingNode` + `TradingNodeConfig`; revisit only on a deliberate Nautilus upgrade.
- **IB live adapter surface confirmed present in the installed wheel:**
  - `InteractiveBrokersDataClient` / `InteractiveBrokersDataClientConfig`
  - `InteractiveBrokersExecutionClient` / `InteractiveBrokersExecClientConfig`
  - `InteractiveBrokersLiveDataClientFactory` / `InteractiveBrokersLiveExecClientFactory`
  - `InteractiveBrokersInstrumentProviderConfig`, `DockerizedIBGatewayConfig`
  - `nautilus_trader.live.reconciliation` module (native reconciliation machinery)

**Architectural decisions inherited from the existing codebase (not re-decided):**

- **Language & runtime:** Python 3.11+, type hints, mypy, ruff (E/F/I, 100 cols), size limits
- **Persistence:** PostgreSQL 16 + TimescaleDB via SQLAlchemy 2.0 (async asyncpg for web, sync
  psycopg2 for CLI — dual-repository rule); Alembic migrations; Redis 7 provisioned in
  docker-compose for Nautilus cache state
- **CLI:** Click command groups under `ntrader`, Rich tables, `--json` options, structlog output
- **Testing:** pytest tiers — unit/component parallel, integration `--forked`, e2e sequential;
  TDD mandatory; broker doubles for all automated tests
- **Config:** Pydantic BaseSettings from env (`IBKRSettings` reused unchanged); UV-only deps
- **Infra:** docker-compose already provides `postgres`, `redis`, and `ib-gateway` (paper)

**Note:** No project initialization story exists — the first implementation story builds directly
in the existing repo on a Phase 3 feature branch (current: `015-paper-trading`).

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**

1. Live host API: Nautilus `TradingNode` (resolved in Starter Template Evaluation)
2. Session persistence model: new `trading_sessions` table + dual-FK `trades` linkage (D1)
3. Safety gate design: two-layer gate with explicit real-money crossing mechanism (D3)
4. Seal-time open-position policy: refuse-by-default, `--force` seals with open trades (D6)
5. Client-ID allocation: dedicated `ibkr_live_client_id` setting (D4)

**Important Decisions (Shape Architecture):**

6. Session runner as asyncio sibling of `BacktestOrchestrator` (D5)
7. Redis-backed Nautilus cache keyed by session-derived `trader_id` (D2)
8. Duplicate prevention: Nautilus reconciliation + query-don't-retry policy (D7)
9. Exit-code contract with dedicated gate-refusal code (D8)

**Deferred Decisions (Post-MVP, recorded so deferral is deliberate):**

- Web UI live monitoring transport (websockets vs polling) — Growth; first genuine websocket candidate
- Multi-session concurrency control — one session at a time this phase
- Divergence tolerance bands / auto-seal — needs real drift data first
- Real-money risk limits and kill switch — belongs to the phase that crosses the gate

### Data Architecture

**D1 — Session persistence: new `trading_sessions` table; runs row created only at seal.**

- New table `trading_sessions` (new Alembic migration): `id` BigInteger PK + `session_id` UUID
  business key (dual-ID pattern, matching `backtest_runs`), `name`, `status`
  (`created|running|stopped|sealed` — DB-enforced enum), `spec` JSONB (immutable after insert),
  `linked_backtest_run_id` (nullable FK → `backtest_runs.run_id`), `sealed_run_id` (nullable, set
  at seal), `last_started_at` / `last_stopped_at` / `sealed_at` timestamps, + `TimestampMixin`.
- **The `backtest_runs` row is created only at seal** — never at session creation. A pre-created
  row would surface unfinished sessions in the existing list views and violate "zero UI changes."
  Before seal, `status`/`list` read from `trading_sessions` + `trades` directly.
- **`backtest_runs` gains `run_type`** (`'backtest'` default | `'paper'`) — satisfies FR44 with one
  column. Execution conditions (starting capital, RTH policy, price-adjustment basis, market data
  tier, closed-trade count — FR43/FR45) ride in the existing `config_snapshot` JSONB under a
  `session_conditions` key: no new columns, forward-compatible, and displayed for free by the
  existing config-snapshot panel.
- **`trades` gains nullable `session_id` FK; `backtest_run_id` becomes nullable**, with CHECK
  constraint (`backtest_run_id IS NOT NULL OR session_id IS NOT NULL`). Live trades are written
  incrementally against `session_id` as positions close (FR28); seal backfills `backtest_run_id`
  on the session's trades, after which existing UI queries see them unchanged. Open positions at
  a `--force` seal persist as open trades (null exit) — the exact shape backtests already use.
- **Spec immutability** is enforced the same way `config_snapshot` is: written once at creation,
  no update path in either repository. Session start re-reads the spec by `session_id` (FR16).
- **Dual-repository rule honored:** `TradingSessionRepository` (async) + `TradingSessionRepositorySync`
  (sync). Only the sync path is exercised this phase; async ships for the Growth monitoring UI.
- **Validation:** the seal path reuses `BacktestPersistenceService` NaN/Infinity validation and
  `DuplicateRecordError` handling verbatim.

**D2 — Nautilus engine state: Redis-backed cache, session-scoped namespace.**

- `TradingNodeConfig(cache=CacheConfig(database=DatabaseConfig(...redis...)))` pointed at the
  provisioned `redis:7-alpine`.
- `trader_id` is derived deterministically from the session (`PAPER-<short-session-id>`), so the
  Redis namespace is per-session: a restarted process rejoins its own cache; two sessions never
  share order/position state. Redis holds *cache* state only — IBKR remains ground truth and
  startup reconciliation overwrites cache on conflict (FR35).
- New `RedisSettings` (host/port/db via env) added to `Settings` — the compose service exists but
  no typed settings do yet.

### Authentication & Security

**D3 — The safety gate: two layers, fail-closed, with a deliberate crossing mechanism.**

- **Layer 1 — pre-connection static gate** (FR9, runs before any socket opens):
  refuses unless *all* hold: `ibkr_trading_mode == "paper"`, `ibkr_port` ∈ known paper ports
  {7497 (TWS paper), 4002 (Gateway paper)}, and `TWS_ACCOUNT` (when set) matches the IBKR paper
  prefix (`DU`/`DF`). Any failure → structured refusal message + dedicated exit code, zero
  connections attempted.
- **Layer 2 — post-connection account verification** (before strategies start, before any order
  path exists): the account ID actually reported by the connected gateway must carry a paper
  prefix. Mismatch → immediate node shutdown, no strategy start, same refusal exit code. This
  catches the case the static gate cannot: a live account listening on a paper port.
- **Deliberate crossing (FR10, designed now, exercised in a future phase):** real-money requires
  BOTH `--real-money` on the CLI AND env `NTRADER_REAL_MONEY_ACCOUNT=<exact account id>` matching
  the connected account. Two independent, redundant declarations across two channels — a port
  typo, stale env var, or copied command can never produce all of them. There is no interactive
  prompt to automate around; absence of either condition is refusal.
- **Gate is a pure, unit-testable function** (`evaluate_gate(settings, cli_flags) -> GateDecision`)
  plus a thin integration seam — the automated test requirement (NFR) falls out naturally.
- `ibkr_read_only=False` is set *only after* Layer 1 passes, scoped to the session process.
- Logging: account IDs masked to last 3 characters; credentials never logged (existing rule).

### API & Communication Patterns

**D8 — CLI contract (no web API work this phase):**

- New Click group `ntrader live` with `create / start / status / list / reconcile / seal`
  (PRD command table adopted as-is). `stop` = Ctrl-C / SIGTERM on the foreground process —
  graceful: cancel subscriptions, stop node, leave positions untouched, mark session `stopped`.
- **Exit codes:** `0` success · `1` general error · `2` usage error (Click default) ·
  `3` **gate refusal** (scriptably distinct, FR11) · `4` broker connectivity failure.
- `status`/`list` render Rich tables with `--json` (FR51), matching existing CLI conventions.
- Structured `structlog` streaming with `session_id` bound into every log record (FR48),
  extending the correlation-ID convention.

**D7 — Order-path integrity policy (cross-cutting, enforced in infrastructure):**

- Client order IDs come from Nautilus's deterministic generator under the session-stable
  `trader_id` — uniqueness and determinism inherited, not reinvented.
- **No retry-on-timeout for order placement, anywhere.** On ambiguity (timeout, disconnect
  mid-submit), the execution layer queries order state; resubmission only after the venue
  confirms the order is not working.
- Startup reconciliation loads working orders into the cache before strategies start, so a
  resumed strategy sees its own open orders (FR27).
- Order events (accepted / partial / filled / rejected / cancelled / expired) are logged with
  venue reasons as they arrive; rejections never raise into strategy silence (FR26, FR31).

### Frontend Architecture

None this phase — zero UI change is an E5 success criterion. Sealed sessions render through the
existing list/detail/comparison views purely because they are ordinary `backtest_runs` rows.
(Recorded implication: `run_type` gives the UI a costless "Paper" badge later — Growth, not now.)

### Infrastructure & Deployment

**D4 — Client-ID allocation:** new `ibkr_live_client_id` field on the existing `IBKRSettings`
(default `10`; historical client keeps `IBKR_CLIENT_ID` default `1`), with a Pydantic model
validator rejecting equality. The two clients can run concurrently (FR5).

**D5 — Process model:** foreground asyncio process, operator-managed (tmux/screen), no daemon.
`ntrader live start` → `asyncio.run(session_runner.run(...))` hosting the `TradingNode`.
SIGINT/SIGTERM handlers trigger graceful node stop; a second signal forces exit. `LogGuard`:
the node's guard is registered via the existing `set_nautilus_log_guard()` before any other
Nautilus component initializes in-process.

- **Gateway/TWS:** operator-run TWS or IB Gateway (existing `ib-gateway` compose service usable);
  the daily restart is handled by the IB adapter's reconnect machinery + the design assumption
  that overnight the process is normally stopped anyway. Reconnect target < 60s (NFR).
- **Market data:** `ibkr_market_data_type=REALTIME` for sessions (explicitly overriding the
  `DELAYED_FROZEN` fetch default); `ibkr_use_rth=True`; instrument-count vs market-data-line
  budget checked at startup with a clear failure (NFR).
- **No CI/CD changes.** Automated tests never touch a broker (existing tiers + broker doubles);
  operator-run verification procedures against the paper account are documented as the phase-gate
  evidence (Testability NFR).

**D6 — Seal-time open positions (PRD-assigned open decision — RESOLVED):**

- `seal` **refuses by default when the session has open positions**, listing them explicitly.
- `seal --force` seals anyway: open positions persist as **open trades (null exit)** — exactly
  how backtests already record open positions — their disposition is printed in the seal report,
  and they remain at the broker, explicitly reported as no longer owned by any session.
- **Sealing never flattens.** Auto-flattening would manufacture artificial exits, violating the
  "no artificial trades" invariant; a deliberate manual flatten in TWS before sealing remains
  available to the operator and produces honest, strategy-external exits visible as such.
- Satisfies FR47's actual requirement: explicit and reported, never silent.

### Decision Impact Analysis

**Implementation Sequence (mirrors E1→E5):**

1. D3 gate (pure function + settings) and D4 client-ID split — before any connecting code (E1)
2. `TradingNode` assembly with IB factories, `LogGuard` registration, REALTIME/RTH config (E1)
3. D1 session migration + session entity + dual repositories + D2 Redis cache config (E2)
4. D5 session runner + CLI group + signal handling + D8 exit codes (E2)
5. D7 order-path policy + incremental trade persistence on position-closed events (E3)
6. Reconciliation configuration + warm-up sequencing + `reconcile` command (E4)
7. D6 seal service + `ResultsExtractor` host widening + `run_type` stamping (E5)

**Cross-Component Dependencies:**

- D2's session-derived `trader_id` is what makes D7's deterministic order IDs session-stable
  across restarts — these two decisions must land together (E2 before E3).
- D1's runs-row-at-seal-only choice forces `status`/`list` to read `trading_sessions` + `trades`,
  not `backtest_runs` — the CLI query path (D8) depends on D1's shape.
- D6 depends on the trade-persist behavior confirmed in Phase 2 (open positions persist with null
  exit) — seal reuses it rather than adding a parallel path.
- D3 Layer 2 runs inside D5's startup sequence — gate evaluation is a startup phase, not a
  one-off check, and its placement (after connect, before strategy start) is testable in the
  session runner's state machine.

## Implementation Patterns & Consistency Rules

_The existing `_bmad-output/project-context.md` (88 rules) governs everything it covers — naming,
layering, dual repositories, TDD tiers, logging, Decimal money, size limits. The patterns below are
only the **net-new Phase 3 conflict points** where agents could otherwise diverge._

### Naming Patterns

**Vocabulary (use these words, never synonyms):**

| Concept | Term | Never |
|---|---|---|
| The logical forward test | **session** | run, test, experiment |
| One process execution | **process run** | session, instance |
| Ending a session permanently | **seal** | close, finalize, complete, end |
| Stopping the process | **stop** | pause, halt, kill, close |
| The paper/live check | **gate** | guard, check, validator |

**Database:** table `trading_sessions` (not `sessions` — too generic; not `paper_sessions` — the
model outlives paper). Columns snake_case per existing convention. Status enum values lowercase:
`created`, `running`, `stopped`, `sealed`. New FK columns follow `<entity>_id` (`session_id`,
`linked_backtest_run_id`).

**Code:** module prefix is `live_` / package `live` (mirrors CLI group `ntrader live`):
`src/core/live_session_runner.py` → `LiveSessionRunner`; `src/services/session_service.py` →
`SessionService`; gate in `src/core/live_gate.py` → `evaluate_gate()`, `GateDecision`,
`GateRefusal`. Domain model `src/models/session.py` → `SessionSpec`, `StrategySpec`,
`SessionStatus` (StrEnum). CLI file `src/cli/commands/live.py`.

**Session IDs:** UUID business key (`session_id`), Pattern matches `run_id`. Human-readable
`name` is operator-supplied, unique, and usable as a CLI argument everywhere a session ID is
(`resolve by name → UUID` in one shared helper).

### Structure Patterns

- **Session lifecycle is one state machine in one place** — `SessionStatus` transitions validated
  in `SessionService` (single `transition()` path). CLI commands and the runner request
  transitions; they never set `status` directly. Illegal transition → domain exception
  `InvalidSessionTransition` (in `src/db/exceptions.py` family).
- **The runner owns the node; the service owns the record.** `LiveSessionRunner` builds/starts/
  stops the `TradingNode` and never touches SQL; `SessionService` owns `trading_sessions` rows and
  never imports Nautilus. The CLI composes them. (Keeps Nautilus imports out of DB-testable code —
  same separation `BacktestOrchestrator`/persistence services already use.)
- **Startup sequence is explicit, ordered, and logged as phases:**
  `gate:static → node:build → node:connect → gate:account → reconcile → warmup → subscribe → trading`.
  Each phase logs `phase=<name> status=started|ok|failed`. Agents must not reorder, merge, or skip
  phases; a failure in any phase stops the sequence.
- **Strategy changes live in strategy files; live semantics live in infrastructure.** The two
  sanctioned strategy edits (indicator warm-up in `on_start()`, removing flatten from `on_stop()`)
  must use only mode-agnostic Nautilus APIs (`register_indicator_for_bars`, `request_bars`,
  `subscribe_bars`). Anything needing live-only handling goes in the runner or a shared base —
  never `if self.is_live:` (grep-enforceable: zero matches in `src/core/strategies/`).
- **Tests:** broker doubles extend `tests/component/doubles/` (e.g. `TestLiveNode`,
  `TestExecClient`); gate unit tests in `tests/unit/core/test_live_gate.py`; session state machine
  unit-tested without Nautilus; operator procedures documented in `docs/qa/phase3-live-verification.md`
  (the phase-gate evidence, mirroring Phase 1/2 QA docs).

### Format Patterns

- **`--json` output:** plain objects, snake_case keys, ISO-8601 UTC timestamps, Decimal-as-string
  for money (existing REST convention). `status --json` top-level keys: `session_id`, `name`,
  `status`, `closed_trade_count`, `open_positions`, `last_activity_at`, `health`.
- **Health vocabulary (FR49, "silence ≠ death"):** `health` ∈ `trading` (connected, bars flowing),
  `idle` (connected, no signals — normal), `degraded` (disconnected/reconnecting), `stopped`.
  Derived, never stored.
- **Seal report** (human + `--json`): run_id created, closed-trade count, open positions and their
  disposition, execution-conditions summary. Same fields both formats.
- **`session_conditions` JSONB schema** (inside `config_snapshot`): `starting_capital`,
  `rth_only`, `price_basis` (`"firstrate_adjusted"` | `"ibkr_unadjusted_smart"`),
  `market_data_tier`, `closed_trade_count`, `open_positions_at_seal`. Versioned with a
  `schema_version: 1` key.

### Communication Patterns

- **Log event naming:** dotted lowercase past-tense events, session-scoped:
  `session.created`, `session.started`, `session.stopped`, `session.sealed`, `gate.refused`,
  `order.submitted`, `order.accepted`, `order.rejected` (with `venue_reason`), `order.filled`
  (with `fill_qty`, `cum_qty`), `trade.persisted`, `reconcile.ok`, `reconcile.discrepancy`,
  `connection.lost`, `connection.restored`, `warmup.completed`. Every record carries bound
  `session_id`; order events also carry `client_order_id` and `instrument_id`.
- **Nautilus events are consumed via the framework's handlers** (`on_order_rejected`,
  `on_position_closed`, etc.) — no polling loops, no parallel event bus. Trade persistence hooks
  the position-closed path.

### Process Patterns

- **Error handling:** strategy exceptions are caught at the runner boundary, logged with
  `strategy_id`, and isolate that strategy (FR50) — never `sys.exit()` from inside the node.
  Broker unreachable → halt trading, log `connection.lost`, keep process alive for reconnect
  window; never proceed on stale state.
- **Order ambiguity:** the ONLY sanctioned recovery is query-order-state; agents must never add
  retry loops around `submit_order` (grep-enforceable: no retry/backoff decorators on the order
  path).
- **DB write failures during trading never kill the node:** trade-persist errors are logged and
  retried on next event (savepoint discipline from the Phase 2 fix); the session must go on
  trading even if Postgres hiccups.
- **Signal handling:** first SIGINT/SIGTERM → graceful stop (unsubscribe, node stop, status →
  `stopped`); second → force exit. Never flatten, never seal, on any signal path.

### Enforcement Guidelines

**All AI agents MUST:**

- Route every status change through `SessionService.transition()` — grep: no direct
  `status =` assignments outside it.
- Keep `src/core/strategies/**` free of live/backtest branching — grep: `is_live` count = 0.
- Keep Nautilus imports out of `SessionService`/repositories; keep SQLAlchemy out of
  `LiveSessionRunner`.
- Add every new DB feature to BOTH repositories (async + sync) in the same story.
- Write the failing test first (TDD) — gate, state machine, and duplicate-prevention logic are
  unit/component tier; anything touching a real `TradingNode` is integration (`--forked`) or an
  operator procedure.

**Anti-patterns (reject in review):**

- A second results vocabulary (any new metrics table/model for paper) — FR42 violation.
- Pre-creating `backtest_runs` rows before seal.
- `close_all_positions()` anywhere in a lifecycle hook.
- Retry-on-timeout around order submission.
- Reading `ibkr_read_only` as the safety control (the gate is the control).

## Project Structure & Boundaries

### Delta Project Tree (brownfield — NEW and MODIFIED only)

```
Trading-ntrader/
├── alembic/versions/
│   └── xxxx_add_trading_sessions_and_paper_run_support.py   # NEW — trading_sessions table;
│                                                            #   backtest_runs.run_type;
│                                                            #   trades.session_id (nullable FK) +
│                                                            #   backtest_run_id → nullable + CHECK
├── src/
│   ├── config.py                          # MOD — IBKRSettings: +ibkr_live_client_id (validator:
│   │                                      #   ≠ ibkr_client_id); +RedisSettings on Settings
│   ├── cli/
│   │   ├── main.py                        # MOD — register `live` group
│   │   └── commands/
│   │       └── live.py                    # NEW — create/start/status/list/reconcile/seal;
│   │                                      #   exit codes (3 = gate refusal, 4 = connectivity)
│   ├── core/
│   │   ├── live_gate.py                   # NEW — evaluate_gate(), GateDecision, GateRefusal;
│   │   │                                  #   pure function, no I/O
│   │   ├── live_node_builder.py           # NEW — TradingNodeConfig assembly: IB data/exec client
│   │   │                                  #   configs, factories, Redis CacheConfig, LogGuard
│   │   │                                  #   registration, REALTIME + RTH settings
│   │   ├── live_session_runner.py         # NEW — owns TradingNode lifecycle; startup phase
│   │   │                                  #   sequence; signal handling; strategy isolation;
│   │   │                                  #   NO SQLAlchemy imports
│   │   ├── results_extractor.py           # MOD — widen host: accept BacktestEngine or live
│   │   │                                  #   (portfolio, cache) pair; metric math untouched
│   │   └── strategies/
│   │       ├── sma_crossover.py           # MOD — on_start(): register_indicator_for_bars +
│   │       │                              #   request_bars(callback=subscribe); on_stop(): remove
│   │       │                              #   close_all_positions(); mode-agnostic only
│   │       └── sma_momentum.py            # MOD — same two lifecycle corrections
│   ├── models/
│   │   └── session.py                     # NEW — SessionSpec, StrategySpec, SessionStatus,
│   │                                      #   SessionConditions (schema_version=1)
│   ├── db/
│   │   ├── exceptions.py                  # MOD — +InvalidSessionTransition
│   │   ├── models/
│   │   │   ├── trading_session.py         # NEW — TradingSession ORM (Base + TimestampMixin,
│   │   │   │                              #   dual-ID: id + session_id UUID)
│   │   │   ├── backtest.py                # MOD — +run_type column
│   │   │   └── trade.py                   # MOD — +session_id FK; backtest_run_id nullable
│   │   └── repositories/
│   │       ├── trading_session_repository.py        # NEW — async (Growth UI)
│   │       └── trading_session_repository_sync.py   # NEW — sync (CLI, this phase)
│   └── services/
│       ├── session_service.py             # NEW — session CRUD + transition() state machine +
│       │                                  #   name→UUID resolution; NO Nautilus imports
│       ├── session_seal_service.py        # NEW — seal: ResultsExtractor → runs row (run_type=
│       │                                  #   'paper') → backfill trades.backtest_run_id →
│       │                                  #   session_conditions snapshot; reuses
│       │                                  #   BacktestPersistenceService validation
│       ├── live_trade_recorder.py         # NEW — position-closed → incremental trade rows
│       │                                  #   (savepoint discipline; never kills the node)
│       └── reconciliation_service.py      # NEW — on-demand positions/cash vs IBKR; explicit
│                                          #   discrepancy report (never auto-resolve local-ward)
├── tests/
│   ├── unit/
│   │   ├── core/test_live_gate.py         # NEW — gate truth table incl. refusal (the NFR test)
│   │   ├── services/test_session_service.py  # NEW — state machine, spec immutability
│   │   └── models/test_session_spec.py    # NEW — spec validation, list-of-strategy-specs shape
│   ├── component/
│   │   ├── doubles/                       # MOD — +TestLiveExecClient, +TestPositionEvents
│   │   ├── test_live_trade_recorder.py    # NEW — partial-fill VWAP aggregation, rejection flow
│   │   └── test_session_runner_phases.py  # NEW — startup phase ordering, signal handling
│   ├── integration/
│   │   └── test_live_node_lifecycle.py    # NEW — --forked; TradingNode + LogGuard coexistence
│   └── e2e/
│       └── test_live_cli.py               # NEW — create→status→list→seal against test DB;
│                                          #   gate refusal exit code 3
└── docs/
    ├── qa/phase3-live-verification.md     # NEW — operator-run procedures (phase-gate evidence)
    └── agent/                             # MOD (post-implementation) — persistence.md,
                                           #   architecture.md updated for sessions
```

**Explicitly untouched (zero-change success criteria):** `src/api/**`, `templates/**`,
`src/services/backtest_query.py`, comparison views, `BacktestOrchestrator`, all import/catalog
commands.

### Architectural Boundaries

**Component boundaries (import direction is the law):**

```
cli/commands/live.py
   ├─▶ services/session_service.py        (record ownership; SQL yes, Nautilus no)
   ├─▶ core/live_gate.py                  (pure; no I/O, no SQL, no Nautilus)
   ├─▶ core/live_session_runner.py        (node ownership; Nautilus yes, SQL no)
   │      └─▶ core/live_node_builder.py ─▶ nautilus_trader.live / adapters.interactive_brokers
   │      └─▶ services/live_trade_recorder.py   (the ONE bridge: Nautilus events → sync repo)
   ├─▶ services/reconciliation_service.py
   └─▶ services/session_seal_service.py ─▶ core/results_extractor.py
```

- `live_trade_recorder` is the single sanctioned place where Nautilus objects meet the DB —
  it converts events to domain models at the boundary; repositories never see Nautilus types.
- Strategies import nothing new — the entire live surface is invisible to them.

**Data boundaries:** PostgreSQL = session records + trades + sealed results (system of record for
history) · Redis = Nautilus cache, session-namespaced by `trader_id`, disposable (rebuildable from
IBKR + reconciliation) · IBKR = ground truth for positions/cash/working orders · Parquet catalog =
untouched by this phase (backtest-side only).

**External integration points:** IBKR TWS/Gateway socket (data + exec clients, dedicated
`ibkr_live_client_id`; historical client unaffected on `IBKR_CLIENT_ID`) · Redis 7 (compose
service) · PostgreSQL (existing).

### Requirements → Structure Mapping

| Epic | FRs | Primary locations |
|---|---|---|
| E1 Connectivity + Gate | FR1–12 | `live_gate.py`, `live_node_builder.py`, `config.py`, `test_live_gate.py` |
| E2 Session Model | FR13–22, FR52–53 | migration, `trading_session.py`, both repositories, `session.py`, `session_service.py`, `live.py`, `live_session_runner.py` |
| E3 Execution + Capture | FR23–31 | `live_trade_recorder.py`, runner order-event handlers, strategy `on_stop()` fix |
| E4 Reconcile + Resume | FR32–39 | `reconciliation_service.py`, node reconciliation config, strategy `on_start()` warm-up |
| E5 Seal + Comparison | FR40–47 | `session_seal_service.py`, `results_extractor.py` widening, `run_type` |
| Cross-cutting | FR48–51 | structlog binding in runner, `status`/`list` in `live.py` |

### Data Flow (live path)

```
IBKR real-time bars ─▶ TradingNode ─▶ Strategy.on_bar() ─▶ submit_order ─▶ IBKR
                                                                    │ fills/rejections
        Redis cache ◀── Nautilus Cache/Portfolio ◀──────────────────┘
                              │ position closed
                              ▼
                    live_trade_recorder ─▶ trades (session_id)          [incremental]
                                                     │ seal
                                                     ▼
   ResultsExtractor(live host) ─▶ backtest_runs(run_type='paper') + performance_metrics
                                                     │
                                                     ▼
                              existing list / detail / comparison UI   [unchanged]
```

### Development Workflow Integration

- Same gates as ever: `make format && make lint && make typecheck`, TDD, pre-commit F401/F821
  hook, `make test-integration` (`--forked`) for anything touching the node.
- Local live-dev loop: compose `redis` + `postgres` up; TWS paper or compose `ib-gateway`;
  `uv run python -m src.cli.main live start <name>` in a terminal (tmux for multi-day).
- No CI changes; no deployment artifacts — the "deployment" is the operator's terminal.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** All choices verified against the installed stack, not docs from
memory: `TradingNode` + IB live factories confirmed present in nautilus-trader 1.220.0; Redis 7
and TimescaleDB/pg16 already provisioned; Click/Rich/structlog conventions carried over. No
version conflicts — the phase adds zero new dependencies (Redis client comes with
nautilus-trader's cache backend; everything else is already in `uv.lock`).

**Pattern Consistency:** The vocabulary table (session/process-run/seal/stop/gate), the
import-direction law, and the single-transition-path rule all reinforce the same three PRD
invariants: one engine framework, stop ≠ seal, broker-authoritative state. No pattern contradicts
a decision; D1's runs-row-at-seal-only is consistently reflected in the CLI query path, the
anti-patterns list, and the delta tree.

**Structure Alignment:** The delta tree respects every existing convention — dual repositories,
domain vs DB models, services layering, test tiers — and the "explicitly untouched" list encodes
the zero-UI-change success criterion structurally.

### Requirements Coverage Validation ✅

Full FR sweep (all 53): FR1–7 → node builder + client-ID split + REALTIME/RTH config; FR8–12 →
two-layer gate + crossing mechanism + exit code 3 + gate unit tests; FR13–22 → session
entity/spec/state machine/CLI; FR23–31 → order-event handlers, VWAP fill aggregation in trade
recorder, query-don't-retry, rejection logging, existing whole-share sizing untouched; FR32–39 →
Nautilus reconciliation config + reconciliation service + warm-up-before-subscribe phase order;
FR40–47 → seal service + extractor widening + `run_type` + `session_conditions` + D6 policy;
FR48–53 → structlog binding, health vocabulary, `--json`, `IBKRSettings`-only credentials.

NFR sweep: reliability invariants each map to a named mechanism (duplicate=0 → D7; resume
discrepancy=0 → reconcile-before-trading phase; zero trade loss → incremental recorder; no blind
trading → phase sequence + connection.lost halt). Performance targets are observable via phase
logs. Testability → gate/state-machine/recorder in unit/component tiers, node lifecycle in
`--forked` integration, operator procedures doc as phase-gate evidence.

### Gap Analysis — Issues Found & RESOLVED (normative addenda)

Four real gaps surfaced during validation. Their resolutions below are **binding amendments** to
the sections above:

**G1 — `status`/`list` health from a separate process (FR22, FR49, "silence ≠ death").**
The CLI runs in a different process from the session runner, so health cannot be answered from
memory. *Resolution:* `trading_sessions` gains `last_heartbeat_at` and `last_bar_at` columns
(added to the D1 migration). The runner updates them (~every 30s / on bar) through the same
record port it uses for transitions. `status` derives health: `stopped` (status ≠ running) ·
`trading`/`idle` (running + fresh heartbeat, split on `last_bar_at` recency) · `degraded`
(running + heartbeat fresh but `connection.lost` flagged) · **`stale`** (status=running,
heartbeat old — likely dead process). `stale` is added to the health vocabulary.

**G2 — Crash while `running` leaves the session stuck (always-valid-state NFR).**
SIGKILL never runs the stop transition. *Resolution:* `start` on a session whose status is
`running` **with a stale heartbeat reclaims it** (logged as `session.reclaimed`); with a fresh
heartbeat it refuses ("another process appears live"). This is the only sanctioned
running→running path and lives in `SessionService.transition()` like every other rule.

**G3 — `reconcile` command client-ID collision (FR5, FR36).**
On-demand reconcile may run while a session process holds `ibkr_live_client_id`. *Resolution:*
the reconcile command connects read-only with `ibkr_live_client_id + 1` (documented reservation:
historical=`IBKR_CLIENT_ID`, session=`ibkr_live_client_id`, reconcile=`+1`), fetches broker
positions/cash, compares against the trades-derived local view, reports explicitly, disconnects.

**G4 — Equity curve source for sealed sessions (metric parity, D6).**
Backtests build the equity curve from the engine; a sealed session has no engine history.
*Resolution:* seal reconstructs the equity series from starting capital + the session's closed-
trade P&L sequence (same units, stored in the existing `PerformanceMetrics` JSON field). Max
drawdown/CAGR/Calmar compute from that series exactly as `ResultsExtractor` already does.
*Recorded limitation:* trade-granularity (not bar-granularity) drawdown — carried in
`session_conditions` so the comparison read stays honest. E5 scope.

### Architecture Completeness Checklist

- [x] Project context analyzed (53 FRs, NFR invariants, brownfield constraints)
- [x] Foundation resolved against installed versions (`TradingNode`, IB factories @ 1.220.0)
- [x] Critical decisions D1–D8 documented with rationale and epic mapping
- [x] PRD-assigned open decisions closed (seal-time positions D6; version check)
- [x] Consistency patterns cover the net-new conflict surface (vocabulary, boundaries, events)
- [x] Delta project tree complete; untouched surface explicit
- [x] FR/NFR coverage swept requirement-by-requirement
- [x] Validation gaps (G1–G4) resolved as binding addenda

### Architecture Readiness Assessment

**Overall Status: READY FOR IMPLEMENTATION** · **Confidence: High** — the riskiest unknowns are
sequenced first (E1), every irreversible failure mode has a named structural control, and the
reuse boundary is drawn so the phase's core claim (comparability) is enforced by construction.

**Key strengths:** one-engine constraint made structural, not aspirational; safety gate as a pure
testable function; session model that makes multi-strategy a config change; append-only reuse of
the results spine (zero UI work).

**Areas for future enhancement (deliberately deferred):** Growth monitoring UI transport,
divergence tolerance bands, real-money gate crossing UX, multi-session concurrency.

### Implementation Handoff

**AI Agent Guidelines:** follow this document + `project-context.md`; on conflict, this document
wins for Phase 3 scope. Escalate (never work around) anything that can't route through Nautilus
abstractions.

**First implementation move (E1):** TDD the gate — `tests/unit/core/test_live_gate.py` truth
table first, then `src/core/live_gate.py`, then `IBKRSettings.ibkr_live_client_id` + validator —
before any connecting code exists.
