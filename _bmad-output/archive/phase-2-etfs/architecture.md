---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-06-17'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/project-context.md'
  - '_bmad-output/archive/phase-1-stocks/architecture.md'
  - 'docs/agent/architecture.md'
  - 'docs/agent/data-pipeline.md'
  - 'docs/agent/nautilus.md'
  - 'docs/agent/web-ui.md'
  - 'docs/agent/persistence.md'
workflowType: 'architecture'
project_name: 'Trading-ntrader'
user_name: 'Allay'
date: '2026-06-17'
effort: 'Phase 2 — FirstRate ETF Import (FMP Metadata Loader)'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

**Effort:** Phase 2 — FirstRate ETF Import (FMP Metadata Loader)

## Project Context Analysis

### Requirements Overview

**Functional Requirements (40 total, 5 epics):**
- **E1 — Instrument Metadata Resolution (FR1-FR6):** Provider-agnostic resolution
  interface (FMP first consumer), persistent cache (no redundant fetches), explicit
  `N/A` sentinel distinguishable from "not yet resolved", per-ticker fault isolation,
  per-import resolution reporting (auto-resolved / descriptive-gap / unresolved-venue).
- **E2 — ETF Data Import (FR7-FR19):** CLI import + dry-run with FMP-aware estimate,
  ZIP extraction stage, reuse of `FirstRateCsvParser` (6-col headerless), all 5 native
  timeframes incl. 30min, Parquet write to isolated FirstRate catalog, OHLC sanity,
  row-count parity verification, sample-point validation, idempotent date-range re-runs,
  timeframe selection, import summary, progress reporting.
- **E3 — Venue Resolution & Completeness Gate (FR20-FR26):** Ticker → Nautilus-qualified
  InstrumentId via resolved venue, unresolved-venue report, `venue_overrides.csv` manual
  corrections with precedence over absent FMP data, merge-on-rerun, deterministic venue
  coverage query, exclude+flag unresolved tickers (bars retained), refuse completion while
  any venue missing (no inferred venues).
- **E4 — Data Explorer ETF Support (FR27-FR32):** ETF ticker list, search/filter, windowed
  chart at all 5 timeframes (incl. 30min), metadata panel with `N/A`-aware rendering,
  per-timeframe statistics, external-reference accuracy verification (TradingView spot-check).
- **E5 — Backtest Integration & Verification (FR33-FR38):** ETF catalog → BacktestEngine via
  `BacktestOrchestrator`, run backtest with existing strategy, whole-share equity sizing,
  multi-ETF runs, results persistence to DB, reference-consistency comparison.
- **Configuration (FR39-FR40):** `FMPSettings` (API key + connection) via typed env config;
  source ETF directory + isolated FirstRate catalog path via config.

**Non-Functional Requirements (architectural drivers):**

| Category | Requirement | Architectural Impact |
|---|---|---|
| Performance | Explorer chart load < 2s | Windowed Parquet reads (Phase 1 pattern preserved) |
| Performance | Ticker list < 500ms, search < 300ms (~5K ETFs) | Indexed metadata-table queries (existing pattern) |
| Performance | Catalog metadata read < 500ms | Read from metadata store, no Parquet directory scans |
| Performance | Import / FMP resolution | No hard target; **rate-limit-bounded**, one-time-then-cached |
| Reliability | Per-ticker fault tolerance (bar *or* metadata) | Failure isolation extends to the FMP resolution step |
| Reliability | Graceful interruption | No partial Parquet readable as valid; consistent on interrupt |
| Reliability | Idempotent recovery + FMP cache | Date-range skip + near-100% cache hit on re-run |
| Reliability | Metadata-store consistency | Resolved metadata + overrides survive restart; no stale serves |
| Reliability | Completeness enforcement | Authoritative, deterministic "is any venue missing?" query |
| Integration | FMP rate-limit compliance | Throttled client; bulk ~5,039 resolution never exceeds quota |
| Integration | Caching contract | Persisted, reused; re-run resolves ≈0 cached tickers |
| Integration | Failure isolation | FMP errors → `N/A` / unresolved-queue, never abort import |
| Integration | Catalog compatibility | ETF Parquet directly consumable, no runtime adapter |
| Integration | Pattern conformance | Click CLI, FastAPI/HTMX DI chain, existing charting stack |
| Security | Localhost personal tool | No authn/authz; FMP key via `FMPSettings`, never committed |
| Maintainability | Project standards | type hints, ruff+mypy, size limits, TDD pyramid, >80% core cov |

**Scale & Complexity:**
- Primary domain: Data pipeline + CLI (primary), light web (explorer ETF display)
- Complexity level: High — but localized to net-new surface (FMP loader, venue gate, `N/A` threading)
- Net-new architectural components: ~3-4 (metadata loader/service, FMP client, ZIP extraction
  stage, venue-override merge) — everything else extends Phase 1 components

### Technical Constraints & Dependencies

**Brownfield Inheritance (not re-opened):**
- Extends Phase 1's `catalog_instruments` table, `instrument_mapper`, `import_service`,
  `CatalogManager`, `MetadataService`, `import_data.py` CLI, and the explorer — Phase 1
  explicitly anticipated ETFs gated on "a metadata loader story (e.g., FMP-backed)."
- `FirstRateCsvParser` reused unchanged for the 6-column ETF schema.
- Parser registry, idempotent metadata-gatekeeper import, dual async/sync repositories,
  Pydantic-settings catalog config, structlog progress — all inherited.
- Catalog isolation (FirstRate never mixed with IBKR/Kraken), single-adjustment-type
  (split+dividend), decimal precision, UTC-normalized timestamps — all carried.

**New External Dependency — FMP (this phase):**
- `FMPSettings` (API key, base URL, rate-limit params) nested under `Settings`,
  mirroring `IBKRSettings`/`KrakenSettings`; never hardcoded.
- Rate-limit-aware client (mirror existing IBKR `RateLimiter`); HTTP client lazy-init
  following `DataCatalogService` lazy-init + caching pattern.
- Persistent metadata cache (correctness + quota cost control); graceful degradation.

**Nautilus / Engine Constraints (carried from Phase 1):**
- BacktestEngine single-use; LogGuard never double-initialized; strict engine setup order.
- Parquet follows `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet`; venue-qualified InstrumentId is
  the precondition for instrument qualification.
- `BacktestOrchestrator` (not `MinimalBacktestRunner`) is the integration path.

**Convention Change:**
- Timeframe enum expands 4→5 (adds 30min), updated centrally; stock 30min backfill
  deferred + tracked outside Phase 2 scope.

### Cross-Cutting Concerns Identified

1. **`N/A` sentinel discipline (three-state):** A field is one of *resolved value*,
   *explicit `N/A`* (FMP had nothing), or *not yet resolved*. Spans metadata loader (writes),
   instrument mapping, explorer presentation models, and backtest config — every consumer
   handles `N/A` without error or silent fallback.
2. **Venue correctness:** The core domain risk. Spans FMP resolution → `venue_overrides.csv`
   merge → instrument qualification → backtest. A wrong/guessed venue silently corrupts
   results; the architecture forbids inference and gates completion on 100% coverage.
3. **Metadata persistence & cache:** One store feeds three readers (explorer display,
   backtest qualification, idempotent re-run) and is written by the loader. Cache is both a
   correctness mechanism (survives re-runs) and a cost control (FMP quota).
4. **Provider-agnostic interface:** The metadata service interface must not leak FMP
   specifics, so the next asset class supplies tickers rather than re-solving sourcing.
5. **5-timeframe catalog convention:** 30min added centrally to the timeframe enum; propagates
   to bar-type strings, explorer selector, statistics, and backtest config.

### Open Architectural Questions (to resolve in later steps)

1. **Metadata store shape:** Extend the existing `catalog_instruments` table with new
   columns (venue source, currency, asset_type, country, ipo_date, resolution status) vs a
   dedicated `instrument_metadata` cache table? Trade-off: reuse vs separation of the
   provider-agnostic cache from catalog identity.
2. **FMP cache location:** PostgreSQL (existing infra, dual-access) vs on-disk file cache.
3. **`venue_overrides.csv` merge mechanics:** Where the merge happens (loader vs import
   orchestration) and how precedence/idempotency is enforced.
4. **Provider interface boundary:** Shape of the `InstrumentMetadataService` abstraction and
   where FMP-specific mapping lives.
5. **ZIP extraction strategy:** Extract-all-then-parse vs extract-per-ticker (bounded disk),
   and how interruption leaves the catalog consistent.

## Starter Template Evaluation

### Primary Technology Domain

Data pipeline + CLI (primary) with light FastAPI/HTMX web (explorer). **Brownfield
extension** of an established backtesting system — no starter template applies.

### Starter Options Considered

**None.** This is a brownfield continuation of Phase 1, building on a mature codebase
(13+ shipped feature specs, Phase 1 ETF-anticipating architecture already in place). The
technology stack, project structure, development patterns, quality gates, and CI are all
established and documented in `project-context.md` (88 rules) and `docs/agent/*`. Selecting
a scaffolding starter would conflict with existing conventions. The architectural foundation
is *inherited*, not generated.

### Inherited Technical Foundation (verified current)

| Layer | Technology | Version | Notes |
|---|---|---|---|
| Runtime | Python | >=3.11 | type hints, mypy-gated |
| Trading engine | nautilus-trader[ib] | >=1.190.0 | C/Rust extensions, LogGuard isolation |
| Web | FastAPI + Jinja2 + HTMX + Tailwind | >=0.121.2 / >=3.1.6 | MPA, TradingView Lightweight Charts |
| DB | PostgreSQL 16 + TimescaleDB, SQLAlchemy | >=2.0.43 | async (web) + sync (CLI) dual repos |
| Validation/config | Pydantic + pydantic-settings | >=2.11.9 / >=2.10.1 | nested settings (IBKR/Kraken) |
| CLI | Click | >=8.2.1 | command groups in `src/cli/commands/` |
| HTTP client | httpx | 0.28.1 (latest) | already present, **dev-group only** |
| Package manager | UV | — | `uv add/remove/sync`; never pip/poetry |
| Quality | ruff, mypy, pytest (forked/xdist/asyncio) | — | TDD, F401/F821 commit gate |

### Net-New Dependency Decision: FMP via `httpx` (no SDK)

- **Decision:** Implement the FMP client with **`httpx`** (sync, matching the CLI's sync
  execution path) — no third-party FMP SDK. FMP's REST API is a handful of simple GET
  endpoints (`/stable/profile`, `/stable/search-exchange-variants`, `/stable/etf-list`);
  an SDK would add an unvetted dependency for trivial JSON-over-HTTP.
- **Action required:** Promote `httpx` from the dev group to a **main runtime dependency**:
  `uv add httpx` (currently `httpx>=0.28.1`, dev-only). It is already used in app code
  (`src/api/ui/backtests.py`), so this also corrects an existing classification gap.
- **Rationale:** Mirrors how `IBKRSettings`/`KrakenSettings` wrap their providers; keeps
  the dependency surface minimal; `httpx` gives sync + async + timeouts + retries hooks.
- **Verified:** httpx 0.28.1 is the current stable release (no version bump needed).

**Note:** No project-initialization story is needed (brownfield). The first implementation
story is instead `FMPSettings` + `uv add httpx`, per the Implementation Patterns section.

### FMP API Reference (verified current, 2026 "stable" routes)

These facts ground the E1 metadata-loader decisions:

- **Base URL:** `https://financialmodelingprep.com/stable` (legacy `/api/v3/` deprecated).
  Auth via `apikey` query param or header.
- **One endpoint resolves every PRD metadata field:** `GET /stable/profile?symbol={T}` →
  `exchange` (listing label), `exchangeFullName` + exchange short name, `currency`,
  `isEtf`/`isFund` (asset type), `companyName`, `sector`, `industry`, `country`, `ipoDate`.
  No multi-endpoint orchestration needed. Null/empty field → `N/A` sentinel; blank
  `exchange` → unresolved-venue queue.
- **Venue gotcha (justifies the manual-override mandate):** FMP `exchange` is the
  *listing-exchange label* ("NASDAQ"/"NYSE"/"AMEX"), NOT the Nautilus venue. SPY's true
  primary venue is ARCA but FMP often reports AMEX/NYSE. Requires an **FMP-label →
  Nautilus-venue-code normalization map** before the `venue_overrides.csv` backstop.
  `GET /stable/search-exchange-variants?symbol={T}` cross-checks listing venues;
  `GET /stable/etf-list` gives the full ETF universe for validation.
- **Subscription (operator's actual plan): paid Starter** = 300 calls/min, 20GB/30-day
  bandwidth, 5yr history, real-time US. ~5,039 per-ticker `profile` calls ≈ 17 min
  one-time, then cached — within quota with throttling. **Per-ticker `profile` is the
  baseline design path.** `GET /stable/profile-bulk?part=N` is often Premium/Ultimate-gated;
  the loader must NOT depend on it (opportunistic only if confirmed on Starter).

## Core Architectural Decisions

### Decision Priority Analysis

**Critical (block implementation):**
- `instrument_metadata` reusable cache table (ADR-1)
- Three-state `N/A` representation (ADR-2)
- FMP client: sync httpx, single `/stable/profile` call, throttle + graceful degradation (ADR-4)
- Provider-agnostic `InstrumentMetadataService` interface (ADR-5)
- Curated FMP→Nautilus venue map + unresolved queue (ADR-6)
- `venue_overrides.csv` merge + completeness gate (ADR-7)
- Per-archive ZIP extract→import→cleanup (ADR-8)
- 30min central timeframe-enum expansion (ADR-9)

**Important (shape architecture):**
- `instrument_metadata` → `catalog_instruments` qualification sync (ADR-3)
- ETF asset-type classification / whole-share sizing reuse (ADR-10)

**Deferred (post-MVP):**
- `/stable/profile-bulk` optimization (Premium/Ultimate-gated; not depended on)
- Stock 30min backfill (tracked, out of Phase 2 scope)
- Supplementary data (dividends/splits) tables — display-only later

### Data Architecture

**ADR-1: Dedicated `instrument_metadata` cache table (reusable keystone)**
- **Decision:** New table, provider-agnostic, keyed by `ticker`. Columns: `ticker` (PK),
  `metadata_provider` ('FMP'), `venue` (nullable, Nautilus venue code), `currency`,
  `asset_type` (ETF/EQUITY/...), `company_name`, `sector`, `industry`, `country`,
  `ipo_date`, `resolution_status` (enum), `resolved_at`, `created_at`/`updated_at`.
  Decoupled from any catalog — resolves/caches independently of bar import.
- **Rationale:** Realizes the PRD's "reusable by design" mandate — the cache is the asset;
  future asset classes (futures/FX/crypto) populate the same table. Survives re-runs;
  serves Journey 5 (FMP cache = no redundant fetch). Inherits `Base` + `TimestampMixin`;
  dual async/sync repositories (Phase 1 pattern).
- **Trade-off accepted:** A join/sync to `catalog_instruments` (ADR-3) vs single-table
  simplicity. Worth it for the reuse + independent-cache properties.
- **Migration:** New Alembic version (5th) — `00X_add_instrument_metadata.py`.
- **Serves:** FR1, FR2, FR5 (provider-agnostic), FR3 (`N/A`), FR20 (mapping), FR24 (coverage).

**ADR-2: Three-state `N/A` representation**
- **Decision:** Each metadata field is exactly one of: (a) **resolved value**, (b) **explicit
  `N/A`** — FMP returned the field empty/null (stored as the literal sentinel string `"N/A"`
  for descriptive fields), or (c) **not yet resolved** — DB `NULL` (no resolution attempt
  recorded). Row-level `resolution_status` enum: `UNRESOLVED` / `RESOLVED` /
  `VENUE_UNRESOLVED`. Venue is never `"N/A"`: it is either a real code or NULL+`VENUE_UNRESOLVED`.
- **Rationale:** Distinguishability is a hard PRD requirement — `N/A` (honest gap) must differ
  from "not loaded" and from blank. The status enum makes the venue gate a trivial query.
- **Serves:** FR3, FR24, FR26; the cross-cutting `N/A` discipline.

**ADR-3: `instrument_metadata` → `catalog_instruments` qualification sync**
- **Decision:** `catalog_instruments` (Phase 1 catalog-identity row) reads venue/descriptive
  fields from `instrument_metadata` at import time to compute the Nautilus-qualified
  `nautilus_id`. `instrument_metadata` is the source of truth for resolved metadata;
  `catalog_instruments` remains the source of truth for per-catalog bar counts/date ranges.
- **Rationale:** Keeps Phase 1's explorer/backtest reads working unchanged while sourcing
  metadata from the new reusable cache. Single writer (import pipeline) avoids drift.

### External Dependency: FMP Integration

**ADR-4: FMP client — sync httpx, one profile call, throttle + graceful degradation**
- **Decision:** `FMPClient` (sync httpx, lazy-init, `FMPSettings`) resolves a ticker with a
  single `GET /stable/profile?symbol={T}`. Rate-limit throttle reuses the IBKR `RateLimiter`
  pattern bounded to the Starter quota (300 calls/min, configurable). Retries with backoff on
  transient errors (`RateLimitExceeded`-style); on timeout/error/partial → degrade: descriptive
  fields → `"N/A"`, missing venue → `VENUE_UNRESOLVED`. **Never aborts the whole import.**
  Cache check (`instrument_metadata` row with `resolution_status=RESOLVED`) precedes any call.
- **Rationale:** PRD rate-limit + caching + failure-isolation contract; one call covers all
  fields (verified). Sync matches the CLI execution path.
- **Trade-off:** `profile-bulk` (faster but tier-gated) deliberately not depended on.
- **Serves:** FR1, FR2, FR4, FR6, FR39; NFR integration (rate-limit, caching, failure isolation).

**ADR-5: Provider-agnostic `InstrumentMetadataService` interface**
- **Decision:** `InstrumentMetadataService` exposes `resolve(ticker) -> InstrumentMetadata`
  (a clean domain model). The FMP-specific HTTP, field mapping, venue normalization, and `N/A`
  mapping all live in an `FMPMetadataProvider` adapter behind the interface. Consumers
  (instrument mapping, explorer, backtest config) depend only on the domain model — no FMP
  types leak out.
- **Rationale:** The reuse mandate — next asset class supplies tickers to the same service,
  swapping/adding a provider without touching consumers.
- **Serves:** FR5; cross-cutting provider-agnostic concern.

### Venue Resolution & Completeness Gate

**ADR-6: Curated FMP→Nautilus venue map + unresolved queue (no guessed venues)**
- **Decision:** A vetted, code-maintained map translates FMP exchange labels to Nautilus venue
  codes (e.g. `NASDAQ→NASDAQ`, `NYSE→NYSE`, documented ETF corrections). Labels confidently in
  the map auto-resolve; ambiguous classes (notably FMP `AMEX` covering the ARCA/BATS/NYSE-American
  split) and blank/missing labels route to the unresolved-venue queue — never guessed.
- **Rationale:** Venue accuracy is the core domain risk; FMP's listing label ≠ Nautilus venue.
  Honors PRD "no inferred venues" strictly, accepting more up-front manual review.
- **Serves:** FR20, FR21, FR25, FR26.

**ADR-7: `venue_overrides.csv` merge + deterministic completeness gate**
- **Decision:** Import/refresh merges `venue_overrides.csv` (ticker→venue, git-tracked) into
  `instrument_metadata`, override taking precedence over absent/ambiguous FMP venue;
  re-merge each run (idempotent). The completion gate is a deterministic query: **0 rows with
  `resolution_status=VENUE_UNRESOLVED` in the backtestable universe.** Unresolved tickers keep
  their bars but are flagged non-backtestable (excluded from qualification).
- **Rationale:** PRD-decided mechanism (Option B); read-only explorer preserved (no web write-path).
- **Serves:** FR21–FR26; NFR completeness enforcement.

### Import Pipeline & Catalog

**ADR-8: Per-archive ZIP extract→import→cleanup**
- **Decision:** Process one ZIP at a time: extract → parse (reuse `FirstRateCsvParser`) →
  write Parquet → verify row count → delete extracted `.txt` before the next archive. Peak disk
  ≈ one batch. Idempotent resume at archive granularity; Phase 1 metadata-gatekeeper keeps any
  partial Parquet invisible to explorer/backtest on interrupt.
- **Rationale:** PRD disk-pressure risk (Journey 5); bounded memory + disk; consistent-on-interrupt.
- **Serves:** FR9, FR16, NFR graceful-interruption/idempotent-recovery.

**ADR-9: 30min via central timeframe-enum expansion (4→5)**
- **Decision:** Add 30min once to the central timeframe enum; it propagates to bar-type strings,
  explorer selector, statistics, and backtest config. ETFs imported at all 5 from the start;
  stock 30min backfill deferred + tracked.
- **Rationale:** Single source of truth prevents 5min/1hour conflation; PRD convention change.
- **Serves:** FR11, FR17, FR29.

### Backtest Integration

**ADR-10: ETF → BacktestOrchestrator, whole-share equity sizing, multi-ETF via instrument list**
- **Decision:** ETF catalog routes through the existing `BacktestOrchestrator`; venue-qualified
  `InstrumentId` is the precondition. Whole-share (equity) sizing reused; leveraged/inverse ETFs
  settle as ordinary shares. Multi-ETF runs use `BacktestDataConfig`'s `instrument_ids` list
  (Phase 1-confirmed capability). Results persist via existing DB path.
- **Rationale:** No new engine decisions; asset_type from FMP (`isEtf`) drives equity sizing.
- **Serves:** FR33–FR38; NFR catalog compatibility.

### Frontend Architecture

**No new decisions.** Explorer reuses Phase 1 patterns (Jinja2 + HTMX fragments, `NavigationState`,
`FilterState`, TradingView charts). 30min added to the timeframe selector (ADR-9); metadata panel
uses `@computed_field` `N/A`-aware display props (ADR-2). Read-only stance preserved (ADR-7).

### Infrastructure & Deployment

**No new decisions.** Existing Docker + PostgreSQL/TimescaleDB sufficient; no new services. FMP is
an outbound HTTPS call from the existing CLI/app process.

### Decision Impact Analysis

**Implementation Sequence:**
1. `FMPSettings` + `uv add httpx` (promote to runtime dep)
2. Alembic migration: `instrument_metadata` table (ADR-1) + `resolution_status` enum (ADR-2)
3. Domain model `InstrumentMetadata` + `InstrumentMetadataService` interface (ADR-5)
4. `FMPMetadataProvider` adapter: profile call, throttle, venue map, `N/A` mapping (ADR-4, ADR-6)
5. Dual repositories for `instrument_metadata`; cache-check-before-fetch
6. Venue override merge + completeness-gate query (ADR-7)
7. ETF import path: per-archive ZIP handling + metadata resolution per ticker (ADR-8) + 30min enum (ADR-9)
8. `catalog_instruments` qualification sync (ADR-3)
9. Explorer 30min + `N/A` rendering; backtest verification (ADR-10)

**Cross-Component Dependencies:**
- `instrument_metadata` (ADR-1) is written by the FMP provider (ADR-4/5/6) + override merge (ADR-7),
  and read by qualification sync (ADR-3), explorer, and backtest.
- The venue map (ADR-6) + override gate (ADR-7) jointly enforce the 100%-coverage precondition for
  backtest qualification (ADR-10).
- 30min enum (ADR-9) is consumed by import (ADR-8), explorer, and backtest config.

## Implementation Patterns & Consistency Rules

### Pattern Scope

`project-context.md` (88 rules) and the Phase 1 architecture's pattern section (parser registry,
file organization, DB/API naming, CLI structure, import error handling, HTMX fragments) remain
authoritative. This section adds **only the Phase-2 conflict points** where agents could diverge.

### Naming — New Modules & Classes (avoid Phase 1 collisions)

- **Reusable metadata package lives OUTSIDE firstrate:** `src/services/metadata/` — it is
  provider- and asset-class-agnostic (the E1 keystone), not FirstRate-specific. Do NOT add it
  under `src/services/firstrate/`.
- **Name collision to avoid:** `src/services/firstrate/metadata_service.py` already defines
  `MetadataService` (Phase 1 `catalog_instruments` CRUD). The new resolution service is
  **`InstrumentMetadataService`** — distinct name, distinct package. Never reuse "MetadataService".
- **Canonical names:** `InstrumentMetadataService` (interface/orchestrator), `FMPMetadataProvider`
  (adapter), `FMPClient` (httpx wrapper), `VenueMap` (`venue_map.py`), domain model
  `InstrumentMetadata` in `src/models/instrument_metadata.py` (Pydantic, alongside existing
  `src/models/catalog.py`).

### Configuration Pattern

- **`FMPSettings`** nested as **`Settings.fmp`**, mirroring `Settings.ibkr`/`Settings.kraken`.
- Field/env names (snake_case field → UPPER env, matching `ibkr_*` style):
  `fmp_api_key` → `FMP_API_KEY` (required, `repr=False`), `fmp_base_url` →
  `FMP_BASE_URL` (default `https://financialmodelingprep.com/stable`),
  `fmp_rate_limit` → `FMP_RATE_LIMIT` (default `300`, per-minute), `fmp_request_timeout`.
- Never hardcoded; `.env` excluded from git (existing convention).

### Database Pattern

- **Table `instrument_metadata`** (snake_case plural-noun, like `catalog_instruments`).
- **PK `ticker`** (provider-agnostic key); all columns snake_case.
- **`resolution_status`** is a Postgres enum with EXACTLY: `UNRESOLVED`, `RESOLVED`,
  `VENUE_UNRESOLVED`. B-tree index on `resolution_status` (powers the O(1) completeness gate).
- **`asset_type`** stored as an enum/string: `ETF`, `EQUITY`, `FUND` (derived from FMP
  `isEtf`/`isFund`). Inherits `Base` + `TimestampMixin`; dual async (web) + sync (CLI) repos.
- **30min catalog column:** add `bar_count_30min` to `catalog_instruments`, matching the
  existing `bar_count_minute`/`bar_count_hourly`/`bar_count_daily` naming.

### `N/A` Sentinel Pattern (cross-cutting — most divergence-prone)

- **Single constant:** `NA_SENTINEL = "N/A"` defined once (in `src/models/instrument_metadata.py`).
  Never write the literal `"N/A"` ad hoc; import the constant.
- **Three states, enforced:** resolved value · `NA_SENTINEL` (FMP returned empty — descriptive
  fields only) · DB `NULL` (never attempted). **Venue is never `NA_SENTINEL`** — real code or
  `NULL` + `VENUE_UNRESOLVED`.
- **Consumers MUST use shared display helpers** (`@computed_field` `display_*` props on the
  presentation model) — never branch on blank/`""`/`None` ad hoc in templates or backtest config.

### Venue Resolution Pattern

- **One source of truth:** `FMP_EXCHANGE_TO_VENUE` dict in `venue_map.py`. Confidently-mapped
  labels (`NASDAQ`, `NYSE`, …) → Nautilus venue code. An explicit `AMBIGUOUS_LABELS` set (incl.
  `AMEX`) and any unmapped/blank label → return `None` → `VENUE_UNRESOLVED`. **No guessed default.**
- **`venue_overrides.csv`:** header exactly `ticker,venue`; path via config; git-tracked. Merge
  is idempotent and takes precedence over absent/ambiguous FMP venue. Merge happens in the import
  orchestration step (not inside `FMPClient`).

### Timeframe Pattern (30min)

- **Bar-type string `30-MINUTE-LAST`** — word form, consistent with `1-HOUR-LAST`/`5-MINUTE`.
- FirstRate filename token `_30min_` → `30-MINUTE-LAST` (extend the existing maps in
  `firstrate/dry_run.py` and `firstrate/import_service.py`). Added once; no inline string literals.

### FMP Client Error & Reporting Pattern

- **Cache-check before fetch:** skip the API call if an `instrument_metadata` row exists with
  `resolution_status=RESOLVED` (quota protection / idempotency).
- **Catch specific httpx exceptions** (`httpx.TimeoutException`, `httpx.HTTPStatusError`) — never
  bare `except`. Map to graceful degradation (`NA_SENTINEL` / `VENUE_UNRESOLVED`); never abort
  the whole import. structlog fields: `ticker`, `provider`, `resolution_status`, `error`.
- **`ResolutionSummary`** domain object with fixed field names: `resolved`, `descriptive_gaps`,
  `venue_unresolved` — surfaced in the CLI summary and per-import logging.

### Enforcement

- Existing ruff/mypy/pytest gates + F401/F821 commit gate.
- Alembic migration review for `instrument_metadata` naming + enum.
- PR checklist: provider behind `InstrumentMetadataService` (no FMP types leaked to consumers),
  `NA_SENTINEL` imported not literal, venue never guessed, 30min added centrally.
- `project-context.md` + Phase 1 patterns remain authoritative for everything else.

## Project Structure & Boundaries

### New Files & Directories (within existing tree)

```
src/
├── config.py                                    # MODIFIED: add FMPSettings, nest Settings.fmp
├── models/
│   └── instrument_metadata.py                   # NEW: InstrumentMetadata domain model, NA_SENTINEL,
│                                                 #      ResolutionStatus + AssetType enums, ResolutionSummary
├── db/
│   ├── models/
│   │   ├── instrument_metadata.py               # NEW: SQLAlchemy model (PK ticker, resolution_status enum)
│   │   └── catalog_instrument.py                # MODIFIED: add bar_count_30min; venue/asset_type sourced from metadata
│   └── repositories/
│       ├── instrument_metadata_repository.py    # NEW: async repo + coverage-gate query
│       └── instrument_metadata_repository_sync.py # NEW: sync repo (CLI import path)
├── services/
│   ├── metadata/                                # NEW PACKAGE: provider-agnostic, reusable (NOT under firstrate)
│   │   ├── __init__.py
│   │   ├── instrument_metadata_service.py       # InstrumentMetadataService (orchestrator + interface)
│   │   ├── fmp_client.py                         # FMPClient: sync httpx + rate-limit throttle
│   │   ├── venue_map.py                          # FMP_EXCHANGE_TO_VENUE dict + AMBIGUOUS_LABELS set
│   │   ├── venue_overrides.py                    # venue_overrides.csv load + idempotent merge
│   │   └── providers/
│   │       ├── __init__.py
│   │       └── fmp_provider.py                  # FMPMetadataProvider adapter (FMP→domain, N/A mapping)
│   └── firstrate/
│       ├── zip_extractor.py                     # NEW: per-archive extract→cleanup (ADR-8)
│       ├── import_service.py                    # MODIFIED: ETF path, per-archive ZIP, 30min, metadata resolution + qualification sync
│       ├── instrument_mapper.py                 # MODIFIED: venue from InstrumentMetadataService → nautilus_id
│       ├── dry_run.py                           # MODIFIED: _30min_ token, ETF, FMP-aware estimate
│       └── metadata_service.py                  # MODIFIED: bar_count_30min handling (Phase 1 catalog CRUD)
├── cli/commands/
│   ├── import_data.py                           # MODIFIED: ETF asset class, ZIP source dir
│   ├── import_reporting.py                      # MODIFIED: resolution summary + unresolved-venue report
│   └── metadata.py                              # NEW: `metadata coverage` / `metadata unresolved` subcommands
├── api/
│   ├── rest/explorer.py                         # MODIFIED: 30min bar type, ETF metadata in chart/stats responses
│   └── ui/explorer.py                           # MODIFIED: 30min selector, N/A-aware metadata panel
templates/explorer/
│   ├── chart_panel.html                         # MODIFIED: 30min timeframe button
│   └── stats_panel.html / supplementary_panel.html # MODIFIED: N/A-aware metadata rendering
alembic/versions/
│   ├── XXXX_add_instrument_metadata.py          # NEW (10th migration): table + resolution_status enum
│   └── YYYY_add_bar_count_30min.py              # NEW: bar_count_30min on catalog_instruments
pyproject.toml                                    # MODIFIED via `uv add httpx` (dev → runtime dep)

tests/
├── unit/
│   ├── services/metadata/
│   │   ├── test_venue_map.py                    # NEW: label→venue, ambiguous routing
│   │   ├── test_fmp_provider.py                 # NEW: FMP JSON→domain, N/A mapping (mocked httpx)
│   │   └── test_instrument_metadata_service.py  # NEW: cache-check-before-fetch, degradation
│   └── models/test_instrument_metadata.py       # NEW: NA_SENTINEL three-state, ResolutionSummary
├── component/services/
│   ├── metadata/test_venue_overrides_merge.py   # NEW: precedence + idempotency
│   └── firstrate/test_zip_extractor.py          # NEW: per-archive extract/cleanup
├── integration/services/firstrate/
│   └── test_etf_import_pipeline.py              # NEW: end-to-end ETF import (--forked)
├── api/test_explorer_etf.py                     # NEW: 30min + N/A rendering endpoints
└── ui/test_explorer_etf_ui.py                   # NEW: agent-browser ETF verification
```

### Architectural Boundaries

**Provider boundary (the reuse seam):**
```
import_service / explorer / backtest config
  → InstrumentMetadataService.resolve(ticker) -> InstrumentMetadata   (domain model only)
      → FMPMetadataProvider (adapter)
          → FMPClient (httpx) → GET /stable/profile
          → VenueMap (label → Nautilus venue | None)
```
No FMP types or raw JSON cross `InstrumentMetadataService`. Swapping/adding a provider touches
only `providers/` + the service wiring.

**Data boundary:**
```
instrument_metadata (reusable cache, PK ticker)         catalog_instruments (per-catalog identity)
  written by: FMPMetadataProvider + venue_overrides merge   written by: import_service (qualification sync)
  read by:    qualification sync, explorer, backtest         read by:    explorer list/stats, chart API, backtest
```
`instrument_metadata` is source of truth for resolved metadata; `catalog_instruments` for bar
counts/date ranges. Single writer per table (import pipeline) → no drift.

**Venue completeness boundary:**
```
FMP venue (VenueMap)  ──┐
venue_overrides.csv   ──┴─merge(precedence: override)→ instrument_metadata.venue + resolution_status
                                                          │
                          coverage gate query: COUNT(resolution_status=VENUE_UNRESOLVED) == 0
```

**Import boundary (per-archive, bounded disk):**
```
zip_extractor (one ZIP) → FirstRateCsvParser → catalog.write_data() → verify row count
  → InstrumentMetadataService.resolve() → upsert instrument_metadata
  → qualification sync → upsert catalog_instruments → delete extracted .txt → next ZIP
```

### Requirements → Structure Mapping

| Epic | Primary locations |
|---|---|
| **E1 Metadata Loader** | `src/services/metadata/**`, `src/models/instrument_metadata.py`, `src/db/models/instrument_metadata.py`, repos, migration |
| **E2 ETF Import** | `firstrate/zip_extractor.py`, `firstrate/import_service.py`, `firstrate/dry_run.py`, `cli/commands/import_data.py` |
| **E3 Venue Gate** | `metadata/venue_map.py`, `metadata/venue_overrides.py`, `instrument_metadata_repository.py` (gate query), `cli/commands/metadata.py`, `import_reporting.py` |
| **E4 Explorer ETF** | `api/rest/explorer.py`, `api/ui/explorer.py`, `templates/explorer/*.html` (all MODIFIED) |
| **E5 Backtest** | No new files — existing `BacktestOrchestrator` + `firstrate/backtest_loader.py`; venue-qualified InstrumentId precondition |
| **Config (FR39-40)** | `src/config.py` (`FMPSettings`), `FirstRateSettings`/`CatalogSettings` (reused) |

### Notes
- Repository dual-pattern: mirror the existing `catalog_instrument_repository` access; add the
  `_sync` variant for the CLI import path (as with `backtest_repository_sync.py`).
- Stale `CLAUDE.md` references "4 migrations" — there are **9**; new ones are #10/#11. Worth a
  doc-sync chore (out of Phase 2 scope).
- Explorer, supplementary tables (`catalog_dividend`, `catalog_stock_split`), and `country`/`state`
  columns already exist from Phase 1 — E4 extends, not creates.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** All 10 Phase-2 ADRs compose without conflict and use only the
existing stack + one promoted dependency (`httpx`). The reuse seam (ADR-5) cleanly contains the
FMP dependency (ADR-4, ADR-6); the two-table split (ADR-1/ADR-3) has a single writer per table;
the venue map + override + gate (ADR-6/7) form one resolution chain feeding backtest qualification
(ADR-10). No contradictory decisions.

**Pattern Consistency:** Naming avoids the Phase 1 `MetadataService` collision
(`InstrumentMetadataService` in a separate package); `FMPSettings` mirrors `IBKR/KrakenSettings`;
DB/enum/timeframe naming follows established conventions. `NA_SENTINEL` + status-enum pattern is
consistent across all consumers.

**Structure Alignment:** Every ADR maps to concrete files; boundaries (provider/data/venue/import)
are explicit; reusable metadata package sits outside `firstrate/` per the reuse mandate.

### Requirements Coverage Validation ✅

**Functional (40/40 mapped):**
- E1 FR1–6 → ADR-1/2/4/5 + `ResolutionSummary`
- E2 FR7–19 → `import_data.py`, `dry_run.py`, `zip_extractor` (ADR-8), reused parser/validator,
  ADR-9 (30min), `import_reporting.py`
- E3 FR20–26 → ADR-3/6 (mapping), `metadata.py` (reports), ADR-7 (override+merge+gate)
- E4 FR27–32 → explorer routes/templates (MODIFIED), ADR-9, ADR-2 (`N/A` display), UI verify test
- E5 FR33–38 → ADR-10 + existing `BacktestOrchestrator`/`backtest_loader`/`compare.py`
- FR39–40 → `FMPSettings` + reused `FirstRate/CatalogSettings`

**Non-Functional:** Performance targets reuse Phase 1 mechanisms (windowed Parquet reads, indexed
metadata queries); the completeness gate gets a dedicated `resolution_status` index. Reliability
(per-ticker isolation extended to FMP, per-archive + metadata-gatekeeper interruption safety,
idempotent + cached recovery, deterministic gate). Integration (throttle/cache/failure-isolation,
catalog compatibility, pattern conformance). Security (`fmp_api_key` `repr=False`, `.env` excluded).

### Implementation Readiness Validation ✅

Decisions documented with rationale/trade-offs; patterns specify the divergence-prone points;
structure names every new/modified file and the test per tier. Versions verified (httpx 0.28.1;
FMP stable routes confirmed current).

### Gap Analysis Results

**Critical Gaps:** None.

**Important (non-blocking, resolve during E1):**
1. **Metadata cache key vs cross-asset ticker collisions.** `instrument_metadata` is keyed by
   `ticker` alone (ADR-1) — clean for the reuse story and fine for ETFs (their symbols are
   effectively distinct from the stock universe). But the symbol namespace *can* overlap across
   asset classes. **Resolution:** key by `ticker` for Phase 2; document `(ticker, asset_type)` as
   the escape hatch if a real collision appears later. Flagged so it's a conscious choice, not an
   accident.

**Minor (implementation notes):**
2. **FMP `/stable/profile` returns a top-level JSON array.** An empty `[]` = ticker unknown to FMP
   → must map to descriptive `N/A` + `VENUE_UNRESOLVED`, not an error. Bake into `FMPMetadataProvider`.
3. **Postgres ENUM via Alembic.** `resolution_status` enum type must be created before the column
   in the migration (and handled on downgrade). Standard Alembic enum care.
4. **ETF universe count is advisory.** The FirstRate ZIP set is the authoritative ticker source
   (~5,039 is approximate); `etf-list` is an optional cross-check, not a gate.

### Architecture Completeness Checklist

- [x] Requirements analyzed; scale/complexity assessed; constraints + cross-cutting concerns mapped
- [x] 10 ADRs documented; stack specified; integration patterns defined; perf approach stated
- [x] Naming/structure/format/process patterns established (Phase-2 deltas on 88 + Phase 1 rules)
- [x] Complete new/modified file tree; boundaries; FR→structure mapping; integration points

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** High — brownfield extension on proven Phase 1 foundations; the only net-new
surface (FMP loader, venue gate, `N/A` threading, ZIP, 30min) is narrow and well-bounded; FMP API
verified against current docs; one identified design choice (cache key) consciously flagged.

**Key Strengths:**
- Reuse seam isolates the FMP dependency — realizes the "reusable keystone" mandate
- Venue gate is deterministic and honors "no guessed venues" strictly
- Bounded-disk per-archive import; idempotent + cached recovery
- Almost all of E4/E5 is reuse, not new code

**Areas for Future Enhancement:**
- `profile-bulk` resolution if a higher FMP tier is adopted
- `(ticker, asset_type)` cache key if cross-asset collisions emerge
- Generalize the loader to the next asset class (futures/FX/crypto)
- Stock 30min backfill (tracked, deferred)

### Implementation Handoff

**AI Agent Guidelines:** Follow the 10 ADRs and Phase-2 patterns exactly; keep FMP types behind
`InstrumentMetadataService`; import `NA_SENTINEL` (never literal); never guess a venue; add 30min
centrally; TDD per tier (unit for map/provider/model, component for override-merge/zip, integration
`--forked` for ETF import, api/ui for explorer).

**First Implementation Priority:** `FMPSettings` + `uv add httpx`, then the `instrument_metadata`
migration (table + `resolution_status` enum), then the domain model + `InstrumentMetadataService`
interface.
