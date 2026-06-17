---
stepsCompleted:
  - 'step-01-validate-prerequisites'
  - 'step-02-design-epics'
  - 'step-03-create-stories'
  - 'step-04-final-validation'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
---

# Trading-ntrader - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for **Trading-ntrader — Phase 2: FirstRate ETF Import (FMP Metadata Loader)**, decomposing the requirements from the PRD and Architecture into implementable stories. (No UX Design document exists for this phase — the web work is a light extension of established explorer patterns with no new UI paradigm.)

## Requirements Inventory

### Functional Requirements

#### Instrument Metadata Resolution (E1)

- **FR1:** System can resolve instrument metadata (venue, currency, asset type, name, sector, country, IPO date) for a given ticker from an external provider (FMP).
- **FR2:** System can persist resolved metadata so subsequent runs reuse it without re-querying the provider.
- **FR3:** System can record an explicit `N/A` value for any descriptive metadata field the provider cannot supply, distinguishable from "not yet resolved."
- **FR4:** System can continue processing remaining tickers when metadata resolution for one ticker fails or returns partial data.
- **FR5:** System can expose its metadata-resolution capability through a provider-agnostic interface reusable by asset classes beyond ETFs.
- **FR6:** System can report metadata-resolution outcomes per import (count resolved automatically, count with descriptive gaps, count with unresolved venue).

#### ETF Data Import (E2)

- **FR7:** Operator can trigger an ETF data import for a specified source directory via CLI command.
- **FR8:** Operator can run a dry-run that scans source archives and reports ticker counts, detected schema, timeframes, date ranges, and estimated disk usage without writing data.
- **FR9:** System can extract FirstRate ETF ZIP archives prior to parsing.
- **FR10:** System can parse the FirstRate 6-column headerless bar schema for ETF tickers.
- **FR11:** System can import all five native ETF timeframes (1min, 5min, 30min, 1hour, 1day).
- **FR12:** System can convert parsed ETF bars into the existing Nautilus-compatible Parquet catalog structure under the isolated FirstRate catalog path.
- **FR13:** System can validate OHLC sanity (high ≥ low, volume ≥ 0) during import and flag invalid rows.
- **FR14:** System can verify import completeness by comparing source row counts against output Parquet row counts per ticker/timeframe.
- **FR15:** System can validate import accuracy by comparing sample data points (first/last rows) between source and output.
- **FR16:** System can detect incomplete prior imports via date-range comparison and re-import only incomplete tickers (idempotent re-runs).
- **FR17:** Operator can select which timeframes to import (one, several, or all).
- **FR18:** System can produce an import summary report (tickers processed, rows imported, failures with reasons).
- **FR19:** System can report import progress during execution (current ticker, counts, errors).

#### Venue Resolution & Completeness Gate (E3)

- **FR20:** System can map ETF tickers to Nautilus-qualified instrument IDs using resolved venue metadata.
- **FR21:** System can produce an unresolved-venue report listing every ticker lacking a venue, with the reason.
- **FR22:** Operator can supply manual venue corrections that take precedence over (absent) provider data.
- **FR23:** System can merge manual venue corrections into the metadata store on re-run/refresh.
- **FR24:** System can report current venue coverage and identify whether any ticker in the backtestable universe lacks a venue.
- **FR25:** System can exclude tickers without a resolved venue from the backtestable universe and flag them, without dropping their imported bars.
- **FR26:** System can refuse to mark the ETF universe complete while any ticker lacks a venue (no inferred/guessed venues).

#### Data Explorer — ETF Support (E4)

- **FR27:** Operator can view imported ETF tickers in the catalog browser with available date ranges and timeframes.
- **FR28:** Operator can search/filter the ETF ticker list by symbol.
- **FR29:** Operator can load a windowed chart for a selected ETF at any of the five native timeframes (including 30min).
- **FR30:** Operator can view a selected ETF's metadata (name, venue, sector, etc.), with `N/A` rendered clearly where unavailable.
- **FR31:** Operator can view basic data statistics for a selected ETF (row count, date range, min/max prices per timeframe).
- **FR32:** Operator can verify ETF bar and metadata accuracy in the explorer against an external reference.

#### Backtest Integration & Verification (E5)

- **FR33:** System can serve imported ETF catalog data to the Nautilus BacktestEngine as a data source.
- **FR34:** Operator can run a backtest against imported ETF data using an existing strategy.
- **FR35:** System can apply whole-share (equity-style) position sizing for ETF backtests.
- **FR36:** Operator can run a backtest spanning multiple ETFs.
- **FR37:** System can persist ETF backtest results to the results database, viewable in the web UI.
- **FR38:** Operator can compare ETF backtest results against a reference path to confirm consistency.

#### Configuration (cross-cutting)

- **FR39:** Operator can supply the FMP API key and connection settings via typed environment-based configuration.
- **FR40:** Operator can configure the source ETF data directory and the isolated FirstRate catalog path via configuration.

### NonFunctional Requirements

#### Performance

- **NFR1:** Explorer chart load (single ETF/timeframe) completes in < 2 seconds.
- **NFR2:** Paginated ticker-list render completes in < 500ms across ~5,000 ETFs.
- **NFR3:** Ticker search/filter completes in < 300ms (keystroke-responsive).
- **NFR4:** Catalog metadata read (enumerate tickers/timeframes without loading price data) completes in < 500ms.
- **NFR5:** Chart scroll/zoom sustains 60fps (native to the charting library's data conflation).
- **NFR6:** Import throughput has no hard target — one-time/occasional batch; prioritize reliability over speed; process one ticker at a time (bounded memory).
- **NFR7:** FMP metadata resolution has no hard latency target but is rate-limit-bounded (throttled to provider quota; cached so cost is paid once, not per re-run).

#### Reliability

- **NFR8:** Import fault tolerance — any single ticker (bar import *or* metadata resolution) can fail without blocking the rest; failures are logged and surfaced in the summary.
- **NFR9:** Graceful interruption — interrupted imports leave the catalog consistent; no partially-written Parquet is readable as valid by the backtest engine.
- **NFR10:** Idempotent recovery — re-running resumes via date-range comparison; the FMP cache prevents redundant re-fetching.
- **NFR11:** Metadata-store consistency — resolved metadata and manual venue overrides survive restarts and re-runs; the explorer never serves stale metadata after a refresh.
- **NFR12:** Completeness enforcement — the venue-coverage check is authoritative; the system can always answer "is any ticker missing a venue?" deterministically.

#### Integration (FMP — primary new surface)

- **NFR13:** Rate-limit compliance — the FMP client throttles to stay within the subscription quota (Starter: 300 calls/min); bulk resolution across ~5,039 tickers never exceeds it.
- **NFR14:** Caching contract — resolved metadata is persisted and reused; a re-run resolves ≈0 already-cached tickers (near-100% cache hit).
- **NFR15:** Failure isolation — FMP timeouts, errors, or partial responses degrade to `N/A` (descriptive) or the unresolved-venue queue (venue), and never abort the whole import.
- **NFR16:** Catalog compatibility — imported ETF Parquet is directly consumable by the Nautilus BacktestEngine with no runtime adapter, following the existing `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet` convention.
- **NFR17:** Existing-pattern conformance — CLI commands follow the existing Click structure; explorer routes follow the existing FastAPI/HTMX DI chain and charting stack.

#### Security

- **NFR18:** Minimal scope — personal localhost tool; no authentication, authorization, or encryption required on the UI/CLI.
- **NFR19:** Credential hygiene — FMP API key supplied via typed settings/env var (`FMPSettings`), never hardcoded or committed; `.env` excluded from git (existing convention).
- **NFR20:** No regulatory burden — KYC/AML, PCI-DSS, GDPR, SOX out of scope (personal tool).

#### Maintainability

- **NFR21:** Conforms to existing standards — Python 3.11+ with type hints, ruff + mypy gates, file/function/class size limits, TDD with the established test pyramid (unit/component/integration `--forked`/e2e), >80% coverage on `src/core` + `src/strategies`.

### Additional Requirements

_Technical requirements and constraints extracted from the Architecture document (10 ADRs + patterns) that shape epic/story implementation._

**Starter Template:** None. This is a **brownfield continuation** of Phase 1 on a mature codebase — no scaffolding starter applies; the architectural foundation is inherited, not generated. **There is therefore no project-initialization story.** The first implementation story is instead `FMPSettings` + `uv add httpx` (promote `httpx` from dev group to runtime dependency, correcting an existing classification gap — already used in `src/api/ui/backtests.py`).

**Data architecture (ADR-1, ADR-2, ADR-3):**
- New provider-agnostic `instrument_metadata` cache table, keyed by `ticker` (PK), decoupled from any catalog. Columns: `metadata_provider`, `venue` (nullable, Nautilus code), `currency`, `asset_type` (ETF/EQUITY/FUND), `company_name`, `sector`, `industry`, `country`, `ipo_date`, `resolution_status` (enum), `resolved_at`, timestamps. Inherits `Base` + `TimestampMixin`; dual async/sync repositories.
- Three-state `N/A` representation enforced: (a) resolved value, (b) explicit `N/A` sentinel string (descriptive fields only), (c) DB `NULL` = not yet resolved. **Venue is never `"N/A"`** — it is either a real code or `NULL` + `VENUE_UNRESOLVED`.
- `resolution_status` Postgres enum with EXACTLY `UNRESOLVED` / `RESOLVED` / `VENUE_UNRESOLVED`; B-tree index powers the O(1) completeness gate.
- `instrument_metadata` is source of truth for resolved metadata; `catalog_instruments` for bar counts/date ranges. `catalog_instruments` reads venue/descriptive fields from `instrument_metadata` at import time to compute the Nautilus-qualified `nautilus_id` (single writer = import pipeline → no drift).
- New Alembic migration (#10) for `instrument_metadata` + enum; new migration (#11) for `bar_count_30min` on `catalog_instruments`. (Postgres ENUM type must be created before the column in the migration, and handled on downgrade.)

**FMP integration (ADR-4, ADR-5, ADR-6):**
- `FMPClient` (sync httpx, lazy-init, `FMPSettings`) resolves a ticker with a single `GET /stable/profile?symbol={T}` (one endpoint covers all PRD metadata fields). Base URL `https://financialmodelingprep.com/stable`.
- Rate-limit throttle reuses the IBKR `RateLimiter` pattern, bounded to the Starter quota (300 calls/min, configurable). Retries with backoff on transient errors. **Must NOT depend on `/stable/profile-bulk`** (often Premium/Ultimate-gated; opportunistic only).
- Cache-check before fetch: skip the API call if an `instrument_metadata` row exists with `resolution_status=RESOLVED`.
- Catch specific httpx exceptions (`httpx.TimeoutException`, `httpx.HTTPStatusError`) — never bare `except`. On error → graceful degradation (`NA_SENTINEL` / `VENUE_UNRESOLVED`); never abort the import. `/stable/profile` returns a top-level JSON array; empty `[]` = ticker unknown → descriptive `N/A` + `VENUE_UNRESOLVED`, not an error.
- Provider-agnostic seam: `InstrumentMetadataService.resolve(ticker) -> InstrumentMetadata` (domain model only). FMP-specific HTTP/mapping/venue-normalization/`N/A` mapping live behind an `FMPMetadataProvider` adapter — no FMP types leak to consumers.
- `ResolutionSummary` domain object with fixed field names: `resolved`, `descriptive_gaps`, `venue_unresolved` — surfaced in CLI summary + per-import logging.

**Venue resolution & gate (ADR-6, ADR-7):**
- Single source of truth `FMP_EXCHANGE_TO_VENUE` dict in `venue_map.py`: confidently-mapped labels (NASDAQ, NYSE, …) → Nautilus venue code. An explicit `AMBIGUOUS_LABELS` set (incl. `AMEX`, which covers the ARCA/BATS/NYSE-American split) and any unmapped/blank label → `None` → `VENUE_UNRESOLVED`. **No guessed default venue.**
- `venue_overrides.csv` (header exactly `ticker,venue`, path via config, git-tracked) merged in the import-orchestration step (NOT inside `FMPClient`); override takes precedence over absent/ambiguous FMP venue; merge is idempotent (re-merge each run).
- Completion gate is a deterministic query: **0 rows with `resolution_status=VENUE_UNRESOLVED`** in the backtestable universe. Unresolved tickers keep their bars but are flagged non-backtestable (excluded from qualification).

**Import pipeline & catalog (ADR-8, ADR-9):**
- Per-archive ZIP handling: process one ZIP at a time — extract → parse (reuse `FirstRateCsvParser` unchanged) → write Parquet → verify row count → delete extracted `.txt` before the next archive. Peak disk ≈ one batch; idempotent resume at archive granularity; Phase 1 metadata-gatekeeper keeps partial Parquet invisible on interrupt.
- New `src/services/firstrate/zip_extractor.py` for per-archive extract→cleanup.
- 30min added **once** to the central timeframe enum; propagates to bar-type strings, explorer selector, statistics, backtest config. Bar-type string `30-MINUTE-LAST` (word form); FirstRate filename token `_30min_` → `30-MINUTE-LAST` (extend existing maps in `firstrate/dry_run.py` and `firstrate/import_service.py`). ETFs imported at all 5 from the start; **stock 30min backfill deferred + tracked, out of Phase 2 scope.**

**Backtest integration (ADR-10):**
- ETF catalog routes through the existing `BacktestOrchestrator` (NOT `MinimalBacktestRunner`); venue-qualified `InstrumentId` is the precondition. Whole-share (equity) sizing reused; leveraged/inverse ETFs settle as ordinary shares. Multi-ETF runs use `BacktestDataConfig`'s `instrument_ids` list. Results persist via the existing DB path. No new engine files.

**Naming / package placement (anti-collision rules):**
- Reusable metadata package lives at `src/services/metadata/` — provider- and asset-class-agnostic; **NOT** under `src/services/firstrate/`.
- Phase 1 `src/services/firstrate/metadata_service.py::MetadataService` (catalog CRUD) must not be confused with the new `InstrumentMetadataService`. Canonical new names: `InstrumentMetadataService`, `FMPMetadataProvider`, `FMPClient`, `VenueMap` (`venue_map.py`), domain model `InstrumentMetadata` in `src/models/instrument_metadata.py`, `NA_SENTINEL = "N/A"` defined once there.

**Configuration (ADR / patterns):**
- `FMPSettings` nested as `Settings.fmp`, mirroring `Settings.ibkr`/`Settings.kraken`. Fields: `fmp_api_key` → `FMP_API_KEY` (required, `repr=False`), `fmp_base_url` → `FMP_BASE_URL`, `fmp_rate_limit` → `FMP_RATE_LIMIT` (default 300/min), `fmp_request_timeout`. Reuse `FirstRateSettings`/`CatalogSettings` for source dir + catalog path.

**Constraints carried from Phase 1 (not re-opened):**
- BacktestEngine single-use; LogGuard never double-initialized; strict engine setup order.
- Catalog isolation (FirstRate never mixed with IBKR/Kraken); single-adjustment-type (split+dividend); decimal precision preserved; UTC-normalized timestamps.
- Explorer is read-only — no web write-path for venue overrides (preserves Phase 1 stance).
- Explorer, supplementary tables (`catalog_dividend`, `catalog_stock_split`), and `country`/`state` columns already exist from Phase 1 — E4 extends, not creates.

**Cache-key design note (flagged, conscious choice):** `instrument_metadata` keyed by `ticker` alone for Phase 2 (clean for ETFs); `(ticker, asset_type)` documented as the escape hatch if cross-asset symbol collisions appear later.

### UX Design Requirements

_Not applicable — no UX Design document exists for Phase 2. The web work is a light extension of established explorer patterns (Jinja2 + HTMX fragments, `NavigationState`, `FilterState`, TradingView charts) with no new UI paradigm. UI-facing requirements are captured under E4 (FR27–FR32) and the `N/A`-rendering / 30min-selector constraints in Additional Requirements._

### FR Coverage Map

- **FR1:** Epic 1 — Resolve instrument metadata for a ticker from FMP
- **FR2:** Epic 1 — Persist resolved metadata for reuse without re-querying
- **FR3:** Epic 1 — Explicit `N/A` for descriptive gaps, distinguishable from "not yet resolved"
- **FR4:** Epic 1 — Per-ticker fault isolation on resolution failure
- **FR5:** Epic 1 — Provider-agnostic, reusable resolution interface
- **FR6:** Epic 1 — Per-import resolution outcome reporting (resolved / descriptive-gap / unresolved-venue)
- **FR7:** Epic 2 — Trigger ETF import for a source directory via CLI
- **FR8:** Epic 2 — Dry-run scan (ticker counts, schema, timeframes, date ranges, disk estimate)
- **FR9:** Epic 2 — Extract FirstRate ETF ZIP archives prior to parsing
- **FR10:** Epic 2 — Parse the 6-column headerless bar schema for ETFs
- **FR11:** Epic 2 — Import all 5 native timeframes (1min, 5min, 30min, 1hour, 1day)
- **FR12:** Epic 2 — Convert bars to Nautilus-compatible Parquet under the isolated catalog path
- **FR13:** Epic 2 — OHLC sanity validation (high ≥ low, volume ≥ 0), flag invalid rows
- **FR14:** Epic 2 — Row-count parity verification (source vs Parquet) per ticker/timeframe
- **FR15:** Epic 2 — Sample-point (first/last row) accuracy validation
- **FR16:** Epic 2 — Idempotent re-runs via date-range comparison
- **FR17:** Epic 2 — Operator selects which timeframes to import
- **FR18:** Epic 2 — Import summary report (processed, imported, failures w/ reasons)
- **FR19:** Epic 2 — Progress reporting during execution
- **FR20:** Epic 3 — Map ETF tickers to Nautilus-qualified instrument IDs via resolved venue
- **FR21:** Epic 3 — Unresolved-venue report (ticker + reason)
- **FR22:** Epic 3 — Manual venue corrections take precedence over absent provider data
- **FR23:** Epic 3 — Merge manual venue corrections into the metadata store on re-run/refresh
- **FR24:** Epic 3 — Report venue coverage; identify any backtestable ticker lacking a venue
- **FR25:** Epic 3 — Exclude+flag unresolved-venue tickers without dropping their bars
- **FR26:** Epic 3 — Refuse completion while any venue is missing (no inferred venues)
- **FR27:** Epic 4 — View imported ETF tickers with date ranges and timeframes
- **FR28:** Epic 4 — Search/filter the ETF ticker list by symbol
- **FR29:** Epic 4 — Windowed chart at any of the 5 timeframes (incl. 30min)
- **FR30:** Epic 4 — View ETF metadata with `N/A` rendered clearly where unavailable
- **FR31:** Epic 4 — View per-timeframe data statistics (row count, date range, min/max prices)
- **FR32:** Epic 4 — Verify ETF bar + metadata accuracy against an external reference
- **FR33:** Epic 5 — Serve imported ETF catalog data to the BacktestEngine
- **FR34:** Epic 5 — Run a backtest against ETF data using an existing strategy
- **FR35:** Epic 5 — Apply whole-share (equity-style) position sizing for ETFs
- **FR36:** Epic 5 — Run a backtest spanning multiple ETFs
- **FR37:** Epic 5 — Persist ETF backtest results to the DB, viewable in the web UI
- **FR38:** Epic 5 — Compare ETF backtest results against a reference path for consistency
- **FR39:** Epic 1 — Supply FMP API key + connection settings via typed env config
- **FR40:** Epic 2 — Configure source ETF directory + isolated catalog path via config

## Epic List

### Epic 1: Instrument Metadata Resolution (Reusable Foundation)
The operator can resolve and persistently cache instrument metadata (venue, currency, asset type, name, sector, country, IPO date) for any ticker via a provider-agnostic service backed by FMP — with explicit three-state `N/A` handling, per-ticker fault isolation, and per-run resolution reporting. This is the reusable keystone built first; later asset classes (futures/FX/crypto) inherit it rather than re-solving metadata sourcing.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR39

### Epic 2: ETF Data Import Pipeline
The operator can import the full FirstRate ETF bundle (ZIP archives, 6-column schema, all 5 native timeframes incl. 30min) into the isolated Parquet catalog via CLI — with dry-run, OHLC/row-count/sample-point verification, idempotent date-range re-runs, timeframe selection, and a summary report. Metadata is resolved per ticker during import via Epic 1.
**FRs covered:** FR7, FR8, FR9, FR10, FR11, FR12, FR13, FR14, FR15, FR16, FR17, FR18, FR19, FR40

### Epic 3: Venue Resolution & Completeness Gate
The operator can drive every ETF ticker to a resolved venue — via the curated FMP→Nautilus venue map, an unresolved-venue report, and a `venue_overrides.csv` merge — and the system enforces the hard gate (0 `VENUE_UNRESOLVED`) before the ETF universe is "complete," flagging unresolved tickers non-backtestable without dropping their bars. This is the Phase 2 signature journey.
**FRs covered:** FR20, FR21, FR22, FR23, FR24, FR25, FR26

### Epic 4: Data Explorer — ETF Support
The operator can browse, search, and chart imported ETFs at all 5 timeframes (incl. 30min) in the explorer, view metadata with clean `N/A` rendering, see per-timeframe statistics, and verify bar + metadata accuracy against an external reference (TradingView).
**FRs covered:** FR27, FR28, FR29, FR30, FR31, FR32

### Epic 5: Backtest Integration & Verification
The operator can run single- and multi-ETF backtests against the FirstRate ETF catalog through the existing `BacktestOrchestrator` with whole-share equity sizing, persist results to the DB (viewable in the web UI), and confirm consistency against a reference path.
**FRs covered:** FR33, FR34, FR35, FR36, FR37, FR38

### Dependency Chain (forward-only)
`E1 → E2 → {E3, E4}`; `E5` requires `E1 + E2 + E3`. Each epic is shippable in sequence; the system stays fully functional for Stocks throughout. The system remains pause-and-resume safe at any epic boundary.

## Epic 1: Instrument Metadata Resolution (Reusable Foundation)

The operator can resolve and persistently cache instrument metadata for any ticker via a provider-agnostic service backed by FMP — with explicit three-state `N/A` handling, per-ticker fault isolation, and per-run resolution reporting. The reusable keystone, built first. (Brownfield: no project-init story; the first story is config + dependency promotion.)

**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR39

### Story 1.1: FMP Configuration & httpx Runtime Dependency

As the system operator,
I want FMP connection settings supplied via typed environment configuration and `httpx` promoted to a runtime dependency,
So that the metadata loader can authenticate to FMP without hardcoded credentials and with a properly-classified HTTP client.

**Acceptance Criteria:**

**Given** a `.env` containing `FMP_API_KEY`
**When** application `Settings` load
**Then** `Settings.fmp` is an `FMPSettings` instance exposing `fmp_api_key`, `fmp_base_url` (default `https://financialmodelingprep.com/stable`), `fmp_rate_limit` (default `300`, per-minute), and `fmp_request_timeout`
**And** `FMPSettings` is nested as `Settings.fmp`, mirroring `Settings.ibkr` / `Settings.kraken`.

**Given** `FMP_API_KEY` is unset
**When** `Settings` load
**Then** a clear Pydantic validation error is raised (the field is required).

**Given** the settings object is logged or `repr()`'d
**When** its representation is produced
**Then** `fmp_api_key` is not exposed (`repr=False`).

**Given** `pyproject.toml`
**When** `uv add httpx` is run
**Then** `httpx` (>=0.28.1) is recorded as a main runtime dependency (no longer dev-group only)
**And** `make lint` and `make typecheck` pass.

### Story 1.2: instrument_metadata Cache Table, Domain Model & Repositories

As the system,
I want a provider-agnostic metadata cache table with a domain model and dual async/sync repositories,
So that resolved metadata persists across runs and is readable by both the web (async) and CLI (sync) execution paths.

**Acceptance Criteria:**

**Given** a new Alembic migration (the 10th)
**When** `alembic upgrade head` runs
**Then** an `instrument_metadata` table exists with PK `ticker` and columns `metadata_provider`, `venue` (nullable), `currency`, `asset_type`, `company_name`, `sector`, `industry`, `country`, `ipo_date`, `resolution_status`, `resolved_at`, `created_at`, `updated_at`
**And** a Postgres `resolution_status` enum with EXACTLY `UNRESOLVED` / `RESOLVED` / `VENUE_UNRESOLVED` is created **before** the column that uses it
**And** a B-tree index exists on `resolution_status` (powers the O(1) completeness gate).

**Given** the migration's downgrade
**When** `alembic downgrade` runs
**Then** the table and the `resolution_status` enum type are dropped cleanly.

**Given** the domain layer
**When** `InstrumentMetadata` is defined
**Then** it is a Pydantic model in `src/models/instrument_metadata.py` with `NA_SENTINEL = "N/A"` defined once, plus `ResolutionStatus` and `AssetType` enums and the `ResolutionSummary` object
**And** the three-state rule is encodable and enforced: a descriptive field is a value or `NA_SENTINEL`; `venue` is a real code or `None` (never `NA_SENTINEL`); an unattempted field is DB `NULL`.

**Given** the persistence layer
**When** async and sync repositories are exercised
**Then** upsert and get-by-ticker work on both
**And** the SQLAlchemy model inherits `Base` + `TimestampMixin` (mirroring `catalog_instrument`).

### Story 1.3: FMP Client with Rate-Limit Throttle & Graceful Degradation

As the system,
I want a sync `httpx` FMP client that fetches a ticker profile within the rate limit and degrades gracefully on error,
So that bulk resolution across ~5,039 tickers never exceeds quota and a provider failure never aborts the import.

**Acceptance Criteria:**

**Given** an `FMPClient` (lazy-init, configured from `FMPSettings`)
**When** `fetch_profile(ticker)` is called
**Then** it issues a single `GET /stable/profile?symbol={ticker}` with `apikey` auth.

**Given** the configured rate limit (default 300/min)
**When** many tickers are resolved in bulk
**Then** requests are throttled (reusing the existing IBKR `RateLimiter` pattern) and never exceed the quota.

**Given** a transient error response
**When** a request fails
**Then** the client retries with backoff.

**Given** an `httpx.TimeoutException` or `httpx.HTTPStatusError`
**When** it occurs
**Then** it is caught specifically (never a bare `except`) and surfaced as a degradation signal — never raised in a way that aborts the whole import.

**Given** `/stable/profile` returns an empty top-level array `[]` (ticker unknown to FMP)
**When** the response is handled
**Then** it is treated as "no data" (to be mapped downstream to descriptive `N/A` + `VENUE_UNRESOLVED`), not as an error.

**Given** unit tests
**When** the client is tested
**Then** `httpx` is mocked and no real network call is made.

### Story 1.4: FMP Metadata Provider — Field/Asset-Type Mapping, Venue Normalization & N/A Mapping

As the system,
I want an `FMPMetadataProvider` that maps FMP profile JSON to the `InstrumentMetadata` domain model, normalizes the venue, and applies the `N/A` sentinel,
So that all FMP-specific mapping is contained in one adapter and no FMP types leak to consumers.

**Acceptance Criteria:**

**Given** a populated FMP profile JSON
**When** the provider maps it
**Then** `companyName`, `sector`, `industry`, `country`, `ipoDate`, and `currency` populate the corresponding domain fields
**And** any absent/empty descriptive field is set to `NA_SENTINEL`.

**Given** the FMP `isEtf` / `isFund` flags
**When** asset type is derived
**Then** `asset_type` is set to the correct `AssetType` (`ETF` / `FUND` / `EQUITY`).

**Given** the FMP `exchange` label
**When** venue normalization runs against `FMP_EXCHANGE_TO_VENUE` in `venue_map.py`
**Then** a confidently-mapped label (`NASDAQ`, `NYSE`, …) yields the corresponding Nautilus venue code and `resolution_status = RESOLVED`.

**Given** a label in `AMBIGUOUS_LABELS` (incl. `AMEX`) or a blank/unmapped label
**When** venue normalization runs
**Then** `venue` is `None` and `resolution_status = VENUE_UNRESOLVED` — no guessed default venue is ever assigned.

**Given** unit tests
**When** the provider and venue map are tested
**Then** the venue map (label→venue + ambiguous routing) and the provider (JSON→domain + `N/A` mapping) are covered with mocked input.

### Story 1.5: Provider-Agnostic Resolution Service with Cache-Check & Fault Isolation

As the system,
I want an `InstrumentMetadataService` that resolves a ticker cache-first through a provider-agnostic interface and isolates per-ticker failures,
So that later asset classes reuse it unchanged and a single ticker failure never blocks the batch.

**Acceptance Criteria:**

**Given** the service interface
**When** `InstrumentMetadataService.resolve(ticker)` is called
**Then** it returns an `InstrumentMetadata` domain model only — no FMP types or raw JSON cross the boundary.

**Given** an existing `instrument_metadata` row with `resolution_status = RESOLVED`
**When** `resolve(ticker)` is called
**Then** the FMP API is NOT called (cache hit) and metadata is returned from the store.

**Given** no cached row for the ticker
**When** `resolve(ticker)` is called
**Then** the `FMPMetadataProvider` is invoked and the result is upserted into `instrument_metadata`.

**Given** the provider raises or degrades for one ticker
**When** a batch is resolved
**Then** the degraded result is recorded and resolution of the remaining tickers continues uninterrupted (per-ticker fault isolation).

**Given** a hypothetical second provider
**When** it is added
**Then** consumers depend only on the interface + domain model, and the swap touches only `providers/` plus service wiring.

### Story 1.6: Per-Import Resolution Summary Reporting

As the operator,
I want a per-import resolution summary,
So that I can see at a glance how many tickers resolved cleanly, had descriptive gaps, or have unresolved venues.

**Acceptance Criteria:**

**Given** a completed batch resolution
**When** the summary is produced
**Then** a `ResolutionSummary` with the fixed fields `resolved`, `descriptive_gaps`, and `venue_unresolved` is returned
**And** the counts accurately reflect the `resolution_status` of the processed tickers.

**Given** the summary
**When** an import completes
**Then** it is surfaced in the CLI output and emitted via structlog with fields `ticker`, `provider`, `resolution_status`, `error`.

## Epic 2: ETF Data Import Pipeline

The operator can import the full FirstRate ETF bundle (ZIP archives, 6-column schema, all 5 native timeframes incl. 30min) into the isolated Parquet catalog via CLI — with dry-run, verification, idempotent re-runs, timeframe selection, and summary reporting. Metadata is resolved per ticker during import via Epic 1.

**FRs covered:** FR7, FR8, FR9, FR10, FR11, FR12, FR13, FR14, FR15, FR16, FR17, FR18, FR19, FR40

### Story 2.1: 30-Minute Timeframe Convention Expansion (4 → 5)

As the system,
I want 30min added once to the central timeframe enum and the catalog schema,
So that ETFs can be imported, charted, and backtested at all 5 native timeframes without conflating 30min with 5min/1hour.

**Acceptance Criteria:**

**Given** the central timeframe enum
**When** 30min is added
**Then** the bar-type string is `30-MINUTE-LAST` (word form, consistent with `1-HOUR-LAST` / `5-MINUTE`) and is defined in exactly one place — no inline string literals elsewhere.

**Given** the FirstRate filename token `_30min_`
**When** the timeframe maps in `firstrate/dry_run.py` and `firstrate/import_service.py` are extended
**Then** `_30min_` resolves to `30-MINUTE-LAST`.

**Given** a new Alembic migration (the 11th)
**When** `alembic upgrade head` runs
**Then** a `bar_count_30min` column is added to `catalog_instruments`, matching the existing `bar_count_minute` / `bar_count_hourly` / `bar_count_daily` naming.

**Given** the existing Phase 1 stock catalog
**When** this story completes
**Then** stock 30min backfill is explicitly NOT performed (deferred + tracked, out of Phase 2 scope) — only the convention/schema is added.

### Story 2.2: Per-Archive ZIP Extraction

As the system,
I want a per-archive ZIP extraction stage that extracts one archive, hands it to the parser, and cleans up before the next,
So that peak disk usage stays bounded to one batch and an interrupted import leaves the catalog consistent.

**Acceptance Criteria:**

**Given** a `src/services/firstrate/zip_extractor.py` component
**When** it processes a set of letter-batched ZIP archives (26 per timeframe)
**Then** it extracts exactly one archive at a time and deletes the extracted `.txt` files before moving to the next archive.

**Given** an import interrupted mid-archive
**When** the run halts
**Then** no partially-written Parquet is left readable as valid (the Phase 1 metadata-gatekeeper keeps partial output invisible to explorer/backtest).

**Given** a re-run after interruption
**When** extraction resumes
**Then** it resumes at archive granularity without manual bookkeeping.

**Given** component tests
**When** the extractor is exercised
**Then** per-archive extract + cleanup behavior is verified.

### Story 2.3: Dry-Run Scan with FMP-Aware Estimate

As the operator,
I want a dry-run that scans the ETF source archives and reports what would be imported without writing data,
So that I can validate the source and estimate disk before committing to a full import.

**Acceptance Criteria:**

**Given** a dry-run command pointed at the ETF source directory
**When** it runs
**Then** it reports ticker counts (~5,039), detected 6-column schema, timeframes present, and per-ticker date ranges — and writes no data to the catalog.

**Given** the scan
**When** disk usage is estimated
**Then** an estimated disk footprint for the full import is reported.

**Given** the dry-run output
**When** it summarizes metadata work
**Then** it provides an FMP-aware estimate (e.g., how many tickers would require resolution vs. are already cached).

### Story 2.4: ETF Import — Parse, Convert & Write to Isolated Catalog

As the operator,
I want to trigger an ETF import for a source directory that parses the bars, resolves metadata, and writes Nautilus-compatible Parquet to the isolated catalog at all 5 timeframes,
So that the ETF bars land in the catalog alongside Stocks.

**Acceptance Criteria:**

**Given** the ETF source directory and isolated catalog path configured via settings (reusing `FirstRateSettings` / `CatalogSettings`)
**When** the operator runs the ETF import CLI command
**Then** the pipeline extracts each archive (Story 2.2), parses the 6-column headerless schema with the reused `FirstRateCsvParser`, and writes bars as Parquet under `FIRSTRATE_CATALOG_PATH` following `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet`.

**Given** the 5 native timeframes are present in the source
**When** the import runs with no timeframe restriction
**Then** all five (1min, 5min, 30min, 1hour, 1day) are imported.

**Given** the `--timeframe` selection option
**When** the operator specifies one or several timeframes
**Then** only the selected timeframes are imported.

**Given** each parsed ticker
**When** it is processed
**Then** `InstrumentMetadataService.resolve(ticker)` (Epic 1) is invoked and the resolved metadata is upserted into `instrument_metadata` (cache-first; no redundant FMP call if already `RESOLVED`).

**Given** the isolated-catalog rule
**When** ETF Parquet is written
**Then** it is never mixed with IBKR/Kraken data, and decimal precision + UTC-normalized timestamps are preserved (Phase 1 discipline).

### Story 2.5: Import Verification — OHLC Sanity, Row-Count Parity & Sample-Point

As the system,
I want per-ticker/timeframe verification during import,
So that zero data loss and import accuracy are provable, not assumed.

**Acceptance Criteria:**

**Given** bars being imported for a ticker/timeframe
**When** OHLC sanity is checked
**Then** rows violating `high ≥ low` or `volume ≥ 0` are flagged.

**Given** a completed ticker/timeframe import
**When** completeness is verified
**Then** the source row count is compared against the output Parquet row count and any mismatch is reported.

**Given** a completed ticker/timeframe import
**When** accuracy is validated
**Then** sample data points (first and last rows) are compared between source and output.

**Given** any verification failure
**When** it is detected
**Then** it is logged and surfaced in the import summary (and does not silently pass).

### Story 2.6: Idempotent Re-runs via Date-Range Comparison

As the operator,
I want re-running the import to skip already-complete tickers and re-import only incomplete ones,
So that recovery after interruption is cheap, repeatable, and free of duplicate data.

**Acceptance Criteria:**

**Given** a prior partial import
**When** the same import command is re-run
**Then** date-range comparison identifies complete vs. incomplete tickers and re-imports only the incomplete ones (Phase 1 behavior preserved).

**Given** already-resolved tickers
**When** the re-run processes them
**Then** the FMP cache prevents redundant metadata re-fetching (near-100% cache hit).

**Given** a re-run that touches nothing
**When** all tickers are already complete
**Then** no Parquet is rewritten and no FMP calls are made.

### Story 2.7: Import Summary & Progress Reporting

As the operator,
I want progress during the import and a summary report at the end,
So that I can monitor a long batch and review exactly what happened.

**Acceptance Criteria:**

**Given** an import in progress
**When** it runs
**Then** progress is reported (current ticker, running counts, errors) via structured logging.

**Given** a completed import
**When** the summary is produced
**Then** it reports tickers processed, rows imported, and failures with reasons
**And** it incorporates the Epic 1 `ResolutionSummary` (resolved / descriptive-gaps / venue-unresolved counts).

## Epic 3: Venue Resolution & Completeness Gate

The operator can drive every ETF ticker to a resolved venue — via the curated FMP→Nautilus venue map, an unresolved-venue report, and a `venue_overrides.csv` merge — and the system enforces the hard gate (0 `VENUE_UNRESOLVED`) before the ETF universe is "complete," flagging unresolved tickers non-backtestable without dropping their bars.

**FRs covered:** FR20, FR21, FR22, FR23, FR24, FR25, FR26

### Story 3.1: Ticker → Nautilus-Qualified InstrumentId via Resolved Venue

As the system,
I want to map an ETF ticker to a Nautilus-qualified `InstrumentId` using its resolved venue, syncing the qualification into `catalog_instruments`,
So that tickers with a known venue become fully-qualified, backtestable instruments.

**Acceptance Criteria:**

**Given** a ticker with a resolved `venue` in `instrument_metadata`
**When** `instrument_mapper` computes its identity
**Then** it produces a Nautilus-qualified `nautilus_id` (e.g. `SPY.ARCA`) sourced from `InstrumentMetadataService`, not from a hardcoded/guessed venue.

**Given** the qualification sync (ADR-3)
**When** import qualification runs
**Then** `catalog_instruments` reads venue/descriptive fields from `instrument_metadata` to compute `nautilus_id`, with `instrument_metadata` remaining the single source of truth for resolved metadata and `catalog_instruments` for bar counts/date ranges (single writer = import pipeline).

**Given** a ticker whose venue is unresolved (`VENUE_UNRESOLVED`)
**When** qualification runs
**Then** no `nautilus_id` is fabricated for it (it is left unqualified, handled by Story 3.5).

### Story 3.2: Unresolved-Venue Report

As the operator,
I want a report listing every ticker that lacks a resolved venue with the reason,
So that I have a concrete, actionable worklist to drive toward 100% venue coverage.

**Acceptance Criteria:**

**Given** imported ETFs with some venues unresolved
**When** the operator runs the `metadata unresolved` CLI subcommand
**Then** it lists every ticker with `resolution_status = VENUE_UNRESOLVED`, including the reason (e.g., blank FMP label, ambiguous `AMEX`, ticker unknown to FMP).

**Given** the report
**When** it is produced
**Then** it is rendered as readable CLI output and is usable as the basis for filling in `venue_overrides.csv`.

### Story 3.3: venue_overrides.csv Merge with Precedence

As the operator,
I want to supply manual venue corrections in `venue_overrides.csv` that the system merges into the metadata store with precedence,
So that I can resolve venues FMP couldn't, without a web write-path.

**Acceptance Criteria:**

**Given** a git-tracked `venue_overrides.csv` with header exactly `ticker,venue` (path via config)
**When** an import or metadata refresh runs
**Then** the file is loaded and merged into `instrument_metadata` during the import-orchestration step (not inside `FMPClient`).

**Given** a ticker present in both FMP data (absent/ambiguous venue) and the override file
**When** the merge runs
**Then** the override venue takes precedence and the row's `resolution_status` becomes `RESOLVED`.

**Given** the same override file
**When** the import is re-run
**Then** the merge is idempotent (re-merging produces the same result, no duplication or drift).

### Story 3.4: Venue Coverage Report & Completeness Gate

As the operator,
I want a deterministic venue-coverage report and a completion gate,
So that I can answer "is any ticker missing a venue?" authoritatively and the system refuses to declare the ETF universe complete until coverage is 100%.

**Acceptance Criteria:**

**Given** the `metadata coverage` CLI subcommand
**When** it runs
**Then** it reports current venue coverage and the exact count of `resolution_status = VENUE_UNRESOLVED` rows in the backtestable universe, backed by the indexed O(1) query.

**Given** any ticker still has `VENUE_UNRESOLVED`
**When** the completion gate is evaluated
**Then** the gate fails (the ETF universe is NOT marked complete) and no venue is inferred or guessed to satisfy it.

**Given** 0 rows with `VENUE_UNRESOLVED`
**When** the completion gate is evaluated
**Then** the gate passes (venue coverage = 100%).

### Story 3.5: Exclude & Flag Non-Backtestable Tickers

As the system,
I want tickers without a resolved venue excluded from the backtestable universe and flagged, while keeping their imported bars,
So that unresolved tickers never silently enter a backtest and their data isn't lost.

**Acceptance Criteria:**

**Given** a ticker with `VENUE_UNRESOLVED`
**When** the backtestable universe is enumerated
**Then** the ticker is excluded and flagged as non-backtestable.

**Given** that same ticker
**When** the catalog is inspected
**Then** its imported Parquet bars are still present (excluded ≠ dropped).

**Given** the ticker later receives a venue (via override merge)
**When** coverage is re-evaluated
**Then** it transitions to backtestable without re-importing its bars.

## Epic 4: Data Explorer — ETF Support

The operator can browse, search, and chart imported ETFs at all 5 timeframes (incl. 30min) in the explorer, view metadata with clean `N/A` rendering, see per-timeframe statistics, and verify bar + metadata accuracy against an external reference. Extends the Phase 1 explorer (Jinja2 + HTMX, `NavigationState`, TradingView charts), read-only stance preserved.

**FRs covered:** FR27, FR28, FR29, FR30, FR31, FR32

### Story 4.1: Browse Imported ETF Tickers

As the operator,
I want imported ETF tickers listed in the catalog browser with their available date ranges and timeframes,
So that I can see which ETFs are in the catalog and what data each has.

**Acceptance Criteria:**

**Given** imported ETFs in the catalog
**When** the operator opens the explorer catalog browser
**Then** ETF tickers are listed alongside (or filterable from) Stocks, each showing available date range and available timeframes.

**Given** the paginated ticker list across ~5,000 ETFs
**When** it renders
**Then** the list render completes in < 500ms (NFR2), reading from the metadata/catalog store without scanning Parquet directories.

### Story 4.2: Search & Filter the ETF Ticker List by Symbol

As the operator,
I want to search/filter the ETF ticker list by symbol,
So that I can quickly find a specific ETF among thousands.

**Acceptance Criteria:**

**Given** the ETF ticker list
**When** the operator types a symbol fragment into the search/filter
**Then** the list narrows to matching tickers via the existing HTMX `FilterState` pattern.

**Given** a keystroke in the filter
**When** results update
**Then** the search/filter responds in < 300ms (NFR3).

### Story 4.3: Chart a Selected ETF at All 5 Timeframes (incl. 30min)

As the operator,
I want to load a windowed chart for a selected ETF at any of the five native timeframes including 30min,
So that I can visually inspect its bars at the resolution I need.

**Acceptance Criteria:**

**Given** a selected ETF
**When** the operator picks a timeframe
**Then** a windowed TradingView chart loads for any of 1min, 5min, 30min, 1hour, 1day, with 30min present in the timeframe selector (sourced from the central enum, Story 2.1).

**Given** a single ETF/timeframe chart request
**When** it loads
**Then** it completes in < 2 seconds (NFR1) via windowed Parquet reads, and scroll/zoom sustains 60fps (NFR5).

### Story 4.4: ETF Metadata Panel with N/A-Aware Rendering

As the operator,
I want a selected ETF's metadata (name, venue, sector, etc.) displayed with `N/A` rendered cleanly where unavailable,
So that I can trust what I see and tell an honest gap apart from a bug.

**Acceptance Criteria:**

**Given** a selected ETF with full metadata
**When** the metadata panel renders
**Then** name, venue, sector, country, etc. display correctly.

**Given** a selected ETF with descriptive gaps
**When** the panel renders
**Then** missing descriptive fields render as a clear `N/A` (via shared `@computed_field` `display_*` props) — never a blank, never ambiguous, and templates never branch on `""`/`None` ad hoc.

**Given** a ticker whose venue is unresolved
**When** the panel renders
**Then** the venue state is shown distinctly from a descriptive `N/A` (venue is never the `N/A` sentinel).

### Story 4.5: Per-Timeframe ETF Data Statistics

As the operator,
I want basic data statistics for a selected ETF per timeframe,
So that I can sanity-check coverage and price ranges before relying on the data.

**Acceptance Criteria:**

**Given** a selected ETF
**When** the operator views its statistics
**Then** row count, date range, and min/max prices are shown per timeframe (including 30min).

**Given** the statistics request
**When** catalog metadata is read
**Then** it completes in < 500ms (NFR4) without loading full price data.

### Story 4.6: Explorer Accuracy Verification vs External Reference

As the operator,
I want an explicit verification that ETF bars and metadata render correctly against an external reference,
So that I can trust the ETF catalog end-to-end before backtesting on it.

**Acceptance Criteria:**

**Given** several ETFs across timeframes (e.g., QQQ daily, IWM 30min, a thinly-traded leveraged ETF 1min)
**When** the operator spot-checks them in the explorer against TradingView (via the `agent-browser` workflow)
**Then** OHLC bars match the external reference.

**Given** ETFs with full and with sparse metadata
**When** their panels are checked
**Then** well-covered ETFs show full name/venue/sector and sparse ones show `N/A` cleanly — no crashes, no blank-vs-`N/A` ambiguity, no mislabeled venue.

**Given** the new 30min timeframe
**When** it is displayed
**Then** it renders correctly alongside the other four timeframes
**And** screenshot evidence is captured for the verification record.

## Epic 5: Backtest Integration & Verification

The operator can run single- and multi-ETF backtests against the FirstRate ETF catalog through the existing `BacktestOrchestrator` with whole-share equity sizing, persist results to the DB (viewable in the web UI), and confirm consistency against a reference path. Reuse-heavy: no new engine files; venue-qualified `InstrumentId` is the precondition.

**FRs covered:** FR33, FR34, FR35, FR36, FR37, FR38

### Story 5.1: Serve ETF Catalog Data to the BacktestEngine

As the system,
I want imported ETF catalog data served to the Nautilus BacktestEngine as a data source,
So that ETFs can be backtested directly from the FirstRate catalog with no adapter layer.

**Acceptance Criteria:**

**Given** an ETF with imported Parquet bars and a venue-qualified `InstrumentId`
**When** a backtest is configured for it
**Then** the ETF catalog data is routed through the existing `BacktestOrchestrator` path and consumed by the BacktestEngine with no runtime adapter (NFR16).

**Given** an ETF whose venue is unresolved (non-backtestable, Story 3.5)
**When** it is targeted for a backtest
**Then** instrument qualification fails fast / it is excluded — an unresolved venue can never silently enter a run.

### Story 5.2: Run a Single-ETF Backtest with Whole-Share Equity Sizing

As the operator,
I want to run a backtest against an imported ETF using an existing strategy with whole-share position sizing,
So that ETFs behave as a first-class, equity-style backtest asset.

**Acceptance Criteria:**

**Given** a venue-qualified ETF and an existing strategy
**When** the operator runs a backtest
**Then** the run completes against the FirstRate ETF catalog data.

**Given** position sizing for the ETF
**When** orders are sized
**Then** whole-share (equity-style) sizing is applied, consistent with Stocks.

**Given** a leveraged or inverse ETF
**When** it is sized
**Then** it settles as ordinary shares using the same whole-share sizing (not crypto/FX rules).

### Story 5.3: Run a Multi-ETF Backtest

As the operator,
I want to run a single backtest spanning multiple ETFs,
So that I can validate strategies like sector rotation across many instruments at once.

**Acceptance Criteria:**

**Given** several venue-qualified ETFs
**When** a backtest is configured with multiple instruments
**Then** the run uses `BacktestDataConfig`'s `instrument_ids` list and completes across all targeted ETFs.

**Given** the multi-ETF run
**When** it executes
**Then** each ETF uses correct, independently-qualified venue/sizing (no cross-instrument venue bleed).

### Story 5.4: Persist ETF Backtest Results to the Database

As the operator,
I want ETF backtest results persisted to the results database,
So that I can review them in the web UI like any other run.

**Acceptance Criteria:**

**Given** a completed ETF backtest (single or multi)
**When** it finishes
**Then** a results record is persisted via the existing DB path.

**Given** the persisted results
**When** the operator opens the web UI
**Then** the ETF backtest results are viewable there, consistent with existing run displays.

### Story 5.5: Reference-Consistency Verification

As the operator,
I want to compare an ETF backtest result against a reference path,
So that I can confirm the ETF catalog path produces consistent results.

**Acceptance Criteria:**

**Given** an ETF backtest result and a reference path (existing loader / comparison tooling)
**When** the operator runs the comparison
**Then** the results are compared and any deviation beyond expected tolerance is reported.

**Given** the comparison passes
**When** it completes
**Then** the ETF catalog path is confirmed consistent with the reference, closing the Phase 2 verification loop.
