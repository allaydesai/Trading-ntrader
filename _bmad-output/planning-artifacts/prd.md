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
  - 'docs/product/PRD.md'
  - '_bmad-output/project-context.md'
  - '_bmad-output/archive/phase-1-stocks/prd.md'
  - '_bmad-output/archive/phase-1-stocks/product-brief-Trading-ntrader.md'
workflowType: 'prd'
documentCounts:
  briefs: 1
  research: 0
  brainstorming: 0
  projectDocs: 3
classification:
  projectType: 'Web App + CLI'
  domain: 'Fintech (algorithmic trading / quantitative analysis)'
  complexity: 'high'
  projectContext: 'brownfield'
effort: 'Phase 2 — FirstRate ETF Import (FMP metadata loader)'
---

# Product Requirements Document - Trading-ntrader

**Author:** Allay
**Date:** 2026-06-17

**Effort:** Phase 2 — FirstRate ETF Import (FMP Metadata Loader)

## Executive Summary

NTrader is a personal algorithmic trading backtester built on Nautilus Trader, with a Parquet market-data catalog, a PostgreSQL results database, and a FastAPI/HTMX web UI. Phase 1 delivered a proven FirstRate Data import pipeline for **Stocks** (7,151 active tickers across 4 timeframes), turning headerless CSV bundles into a Nautilus-consumable catalog. That phase surfaced one structural blocker for the next asset class: the FirstRate **ETF** bundle ships **without** a `company_profiles.csv`, so there is no ticker→exchange mapping to qualify ETF instruments for backtesting.

Phase 2 unblocks the ETF asset class (**~5,039 tickers**, identical 6-column headerless schema already handled by `FirstRateCsvParser`, delivered as 26 **letter-batched ZIP archives** per timeframe across **5 native timeframes** — 1min, 5min, 30min, 1hour, 1day) by building the missing piece: a **reusable, FMP-backed instrument-metadata loader**. The loader resolves ETF tickers to Nautilus-qualified instrument IDs (venue, currency, asset type) and enriches them with descriptive metadata (name, sector, country, IPO date) from Financial Modeling Prep via the operator's existing subscription. The delivered moment: **import all ETFs and run a multi-ETF backtest from the FirstRate catalog**, with the same position-sizing and verification discipline established in Phase 1.

The primary (sole) user is the system operator, who needs ETFs backtestable from the same trusted catalog as stocks — no manual metadata wrangling, no data-quality doubts. No multi-user, authentication, or community features are in scope.

### What Makes This Special

The CSV→Parquet pipeline is already built and proven, so Phase 2's real product is the **metadata layer**, and its discipline around incomplete data is the differentiator. Two principles govern it:

- **Reusable by design.** FMP is the concrete first provider, but the loader is built as a general instrument-metadata service. ETFs are its first consumer; later FirstRate phases (futures, FX, crypto, indices, delisted stocks) inherit it rather than re-solving metadata sourcing each time. Metadata sourcing — not CSV parsing — is the true gate for every remaining asset class.
- **Honest about gaps.** Where FMP lacks data, the loader writes an explicit `N/A` sentinel — never a silent drop or a guessed value — and **every consumer of metadata (instrument mapping, explorer display, backtest config) must handle `N/A` correctly.** Descriptive fields may remain `N/A`. But the **venue field is mandatory**: a ticker without a venue cannot be qualified as a Nautilus instrument and therefore cannot be backtested. Unresolved venues are surfaced in a report for **manual override**, and **Phase 2 is not complete until every ETF ticker has a resolved venue.**

### Catalog Convention Change

Phase 2 expands the catalog timeframe convention from **4 to 5 timeframes** (adds 30min). ETFs are imported at all 5 from the start. **Backfilling 30min into the existing Phase 1 stock catalog is a tracked pending item**, deferred outside Phase 2's delivery scope.

## Project Classification

- **Type:** Web App + CLI — extends the proven FirstRate import pipeline and explorer; adds a reusable FMP-backed instrument-metadata loader and ZIP-archive extraction
- **Domain:** Fintech — algorithmic trading / quantitative analysis
- **Complexity:** High — new external API dependency (FMP rate limits, caching, partial-coverage handling), ETF instrument mapping with no bundled profiles file, mandatory `N/A` handling across all metadata consumers
- **Context:** Brownfield — continues Phase 1 of the FirstRate import effort; same catalog, same pipeline, second asset class

## Success Criteria

### User Success

- **Primary success moment:** Configure and run a **multi-ETF backtest** against the FirstRate catalog and it just works — instruments are correctly qualified, position sizing is correct (whole-share equity), no manual metadata wrangling.
- **Confidence moment:** Open the data explorer, pick any ETF, and see correct bars *and* correct metadata (name, venue, sector) — or an honest `N/A` where FMP had nothing — with everything rendering accurately at all 5 timeframes.
- **No silent corruption:** The operator never has to wonder whether a backtest used a wrong exchange — every backtestable ETF has a verified venue, and any ticker that couldn't be resolved is visibly flagged, not silently guessed.

### Business Success

- **Phase complete:** All ~5,039 ETF tickers imported across all 5 native timeframes (1min, 5min, 30min, 1hour, 1day), every ticker has a resolved venue, and at least one multi-ETF, multi-year backtest has run successfully using exclusively FirstRate ETF catalog data.
- **Reusable asset proven:** The FMP metadata loader is demonstrably general — adding the *next* asset class requires supplying its tickers to the loader, not rewriting metadata sourcing.
- **Pattern repeatable:** ETF import reuses the Phase 1 pipeline (parser, Parquet writer, verification) with only ZIP-extraction and the metadata loader as net-new components.

### Technical Success

- **100% venue coverage (hard gate):** Phase 2 cannot be marked done while any ETF ticker lacks a venue. The system detects, reports (unresolved-venue list), and accepts manual venue overrides until coverage is complete.
- **Disciplined `N/A` handling:** Descriptive metadata absent from FMP is written as an explicit `N/A` sentinel; instrument mapping, explorer display, and backtest config all handle `N/A` without error or silent fallback.
- **ZIP extraction:** Pipeline extracts the 26 letter-batched ZIP archives per timeframe before parsing, leaving the catalog consistent on interruption.
- **Idempotent re-runs:** Re-running import skips complete tickers and re-imports incomplete ones via date-range comparison (Phase 1 behavior preserved).
- **Import verification:** Row-count parity (source TXT vs Parquet), OHLC sanity (high ≥ low, volume ≥ 0), and sample-point validation (first/last rows) per ticker/timeframe.
- **Catalog parity:** Imported ETF Parquet is directly consumable by the Nautilus BacktestEngine with no adapter layer, following the existing `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet` convention.
- **FMP resilience:** Loader is rate-limit-aware, caches resolved metadata (no redundant API calls on re-run), and degrades gracefully on FMP errors/timeouts without aborting the whole import.
- **Explorer accuracy verification:** An explicit verification task confirms ETF bars and metadata render correctly in the explorer (spot-checked vs TradingView, same discipline as Phase 1).

### Measurable Outcomes

- ~5,039 ETF tickers × 5 timeframes importable with zero manual intervention *except* venue-gap resolution.
- **Venue coverage = 100%** before Phase 2 sign-off (0 tickers with missing venue in the backtestable universe).
- FMP metadata cache hit-rate on re-run ≈ 100% (no re-fetching already-resolved tickers).
- Zero data loss: row-count verification passes on every imported ticker/timeframe.
- Explorer chart loads < 2 seconds for any single ETF/timeframe (Phase 1 performance target preserved).
- At least one multi-ETF backtest completes and produces a results record in the DB.

## Product Scope

### MVP — Minimum Viable Product

The whole of Phase 2 *is* a vertical slice: **ETF import (5 timeframes, ZIP extraction) + FMP metadata loader (with venue gate) + multi-ETF backtest verification + explorer accuracy verification.** Nothing smaller is independently useful — without metadata, ETFs can't be qualified; without the backtest, the import isn't proven.

### Growth Features (Post-MVP)

- 30min backfill into the existing Phase 1 **stock** catalog (tracked pending item).
- ETF supplementary data (4,175 dividend files, 564 split files) surfaced in the explorer.
- Generalized loader applied to the next asset class (Futures / FX / Crypto / Indices / Delisted).

### Vision (Future)

- FMP fundamentals/holdings surfaced in the explorer for ETFs.
- Cross-asset correlation and custom-universe construction spanning stocks + ETFs.
- IBKR/Kraken gap-filling for the FirstRate ETF catalog.

## User Journeys

### Journey 1: ETF Import with Metadata Resolution (Happy Path)

**Allay, solo quant trader** — has the FirstRate ETF bundles on disk (1min, 5min, 30min, 1hour, 1day) as letter-batched ZIP archives. Wants them in the catalog, backtestable, the same way Stocks already are.

**Opening Scene:** The ETF directory holds 26 ZIPs per timeframe. Unlike Stocks, there's **no `company_profiles.csv`** — so there's no built-in ticker→exchange mapping. Last phase this was the hard stop. This phase it isn't.

**Rising Action:** Allay sets `FIRSTRATE_CATALOG_PATH` and the FMP API key, points the CLI at the ETF source, and runs a dry-run. Validation extracts ZIP manifests, reports ~5,039 tickers × 5 timeframes, confirms the 6-column schema, and estimates disk. Allay launches the real import. The pipeline extracts each ZIP, parses the `.txt` bars, and — for each ticker — calls the **FMP metadata loader**, which resolves venue, currency, asset type, and descriptive fields, caching every result.

**Climax:** Import completes. Summary: tickers imported, rows verified against source, FMP resolution stats — *X resolved automatically, Y venues unresolved and queued for manual review*. The bars are all in the catalog; metadata is attached where FMP had it.

**Resolution:** Allay opens the explorer, finds SPY, sees correct bars at all 5 timeframes plus name/venue/sector. The catalog now holds ETFs alongside Stocks. **One loose end remains: the unresolved venues** (Journey 2).

> *Reveals:* CLI ETF import, ZIP extraction, dry-run with FMP-aware estimate, FMP metadata loader with caching, resolution summary reporting.

### Journey 2: Venue Gap Resolution (The Phase 2 Signature Journey)

**Opening Scene:** The import summary flags, say, 140 ETF tickers FMP couldn't resolve a venue for — delisted, obscure, or renamed. Per the hard rule, **Phase 2 isn't done while any of these lack a venue.** These tickers are imported (bars present) but **not yet backtestable**.

**Rising Action:** Allay pulls the **unresolved-venue report** (a clear list: ticker, what FMP returned, why it failed). For each, Allay determines the correct venue manually (TradingView, issuer site, ARCA/NASDAQ/BATS) and supplies a **manual venue override** through the provided mechanism. Descriptive gaps (sector, IPO date) are left as `N/A` — that's acceptable; venue is not.

**Climax:** Allay re-checks coverage. The system reports **0 tickers missing venue.** Every ETF is now a fully-qualified Nautilus instrument.

**Resolution:** The backtestable ETF universe is complete and trustworthy. No silent guesses, no wrong-exchange landmines. The venue gate is satisfied — Phase 2's defining acceptance criterion is met.

> *Reveals:* unresolved-venue detection + report, manual venue override mechanism, coverage check / completion gate, `N/A` tolerance for descriptive fields only.

### Journey 3: Multi-ETF Backtest (Core Success Moment)

**Opening Scene:** Allay has a sector-rotation strategy to validate across 30 ETFs over 8 years — impractical when each ticker meant a broker download.

**Rising Action:** Allay configures a backtest over 30 ETFs from the FirstRate catalog, daily bars, 2016–2024, whole-share equity sizing. Every targeted ETF has a resolved venue (Journey 2 guaranteed it), so all instruments qualify cleanly. No download wait — the data's already local.

**Climax:** Backtests complete across all 30 ETFs with correct position sizing. Results land in the DB, viewable in the web UI.

**Resolution:** Allay iterates freely — swap strategies, widen the universe to 100 ETFs — minutes per loop, not hours. ETFs are now a first-class backtest asset class.

> *Reveals:* FirstRate ETF catalog → BacktestEngine integration, instrument qualification depends on venue completeness, equity position sizing, results persistence.

### Journey 4: Explorer Accuracy Verification (with Honest Gaps)

**Opening Scene:** Before trusting ETF backtests, Allay wants to confirm both **bars and metadata** render correctly — including the `N/A` cases.

**Rising Action:** Allay opens the explorer, spot-checks several ETFs across timeframes against TradingView (QQQ daily, IWM 30min, a thinly-traded leveraged ETF 1min). Confirms OHLC matches. Then checks metadata display: well-covered ETFs show full name/venue/sector; sparse ones show `N/A` cleanly — no crashes, no blank-vs-`N/A` ambiguity, no mislabeled venue.

**Climax:** Bars match TradingView; metadata renders correctly whether present or `N/A`; the new 30min timeframe displays properly alongside the others.

**Resolution:** Allay trusts the ETF catalog end to end. The accuracy-verification task passes.

> *Reveals:* explorer ETF support, 5-timeframe display (incl. 30min), `N/A` rendering discipline across the UI, TradingView spot-check workflow.

### Journey 5: Failed Import Recovery & Re-run (Idempotent + FMP Cache)

**Opening Scene:** Midway through importing all ETFs, the machine runs low on disk. The import halts.

**Rising Action:** Allay frees space and re-runs the exact command. Date-range comparison skips complete tickers and re-imports incomplete ones (Phase 1 behavior). Critically, the **FMP cache** means already-resolved tickers aren't re-fetched — no wasted API calls against the rate limit, no duplicate cost.

**Climax:** Import resumes, touching only the unfinished tickers and unresolved metadata. No checkpoint files, no manual bookkeeping.

**Resolution:** Import completes; summary covers only the delta. Idempotent, cache-efficient, repeatable.

> *Reveals:* idempotent re-run via date-range comparison, FMP metadata cache (no redundant fetches), consistent catalog on interruption.

### Journey Requirements Summary

| Journey | Key Capabilities Revealed |
|---|---|
| 1 — ETF Import w/ Metadata | CLI ETF import, ZIP extraction, dry-run, FMP loader + cache, resolution summary |
| 2 — Venue Gap Resolution | Unresolved-venue report, manual venue override, 100%-coverage completion gate, `N/A` (descriptive only) |
| 3 — Multi-ETF Backtest | ETF catalog → BacktestEngine, venue-gated instrument qualification, equity sizing, results persistence |
| 4 — Explorer Verification | Explorer ETF support, 5-timeframe (incl. 30min) display, `N/A` rendering, TradingView spot-check |
| 5 — Recovery & Re-run | Idempotent date-range re-import, FMP cache efficiency, consistent-on-interrupt catalog |

## Domain-Specific Requirements

### Data Integrity (carried from Phase 1)

- **Decimal precision preserved** through TXT → Parquet conversion — no floating-point rounding artifacts. The existing decimal-arithmetic discipline must not be broken by the ETF path.
- **Timestamp integrity** — ETF bars use the standard `YYYY-MM-DD HH:MM:SS` format (same as Stocks); parser must produce consistent UTC-normalized timestamps. The new **30min** timeframe must be bucketed correctly and not conflated with 5min/1hour.
- **Zero data loss** — row-count parity between source `.txt` and output Parquet for every ticker/timeframe.
- **Catalog isolation** — ETF data lives under `FIRSTRATE_CATALOG_PATH`, never mixed with IBKR/Kraken (different adjustment methodologies). FirstRate ETF data is split+dividend adjusted; this single-adjustment-type rule holds.
- **Idempotent operations** — re-running import produces the same result; no duplicate data, no corruption from interrupted runs.

### Instrument Identity Correctness (the Phase 2 focus)

- **Venue accuracy is the core domain risk.** A wrong venue (e.g. `SPY.NASDAQ` instead of `SPY.ARCA`) silently produces wrong backtest results. Unlike Stocks, there is **no bundled authoritative source** — FMP is the authority, with manual override as the correctness backstop.
- **No guessed venues.** Defaulting an unresolved ticker to a plausible exchange is forbidden — it manufactures false confidence. Unresolved venues are reported and resolved manually, never inferred.
- **Mandatory venue completeness.** The backtestable ETF universe must have 100% venue coverage before sign-off (the hard gate). Tickers lacking a venue remain in the catalog as bars but are excluded from the backtestable set and flagged.
- **`N/A` sentinel discipline.** Descriptive metadata gaps are explicit `N/A`, distinguishable from "not yet loaded" and from empty/blank. Every consumer (mapping, explorer, backtest config) handles `N/A` without error or silent substitution.
- **ETF asset-type classification.** Instruments are qualified as equity-style (ETF) for whole-share position sizing — consistent with Stocks, not crypto/FX sizing rules. Leveraged/inverse ETFs settle as ordinary shares and use the same whole-share sizing.

### External Dependency Constraints (FMP — new this phase)

- **Rate-limit awareness** — FMP enforces request quotas; the loader throttles and never floods the API across ~5,039 tickers.
- **Caching as correctness *and* cost control** — resolved metadata is persisted; re-runs hit cache, not the API. Protects the subscription quota and makes Journey 5 (recovery) cheap.
- **Graceful degradation** — FMP timeouts/errors/partial responses degrade to `N/A` (descriptive) or the unresolved-venue queue (venue); they never abort the whole import.
- **Credential hygiene** — FMP API key supplied via typed settings / env var (`FMPSettings` pattern, mirroring `IBKRSettings`/`KrakenSettings`), never hardcoded or committed.

### Out of Scope (Personal Tool)

KYC/AML, PCI-DSS, GDPR, SOX, audit trails, fraud prevention, and multi-user data protection do not apply. No authentication or authorization on the localhost UI/CLI.

## Web App + CLI Specific Requirements

### Project-Type Overview

Phase 2 touches both surfaces of the existing system, but unevenly:

- **CLI (primary work):** the import pipeline is a Click command, run as a scriptable one-time/occasional batch operation — not interactive. Phase 2 adds ETF import, ZIP extraction, FMP metadata resolution, dry-run, the unresolved-venue report, and the manual venue-override path.
- **Web (light work):** the explorer is server-rendered Jinja2 + HTMX (MPA, no SPA). Phase 2 adds ETF display — 5-timeframe support (incl. 30min), metadata panel with `N/A` rendering — reusing existing chart/route patterns. No new UI paradigm.

Established conventions (unchanged, inherited): **MPA server-rendered HTMX** (no SPA/PWA), **single browser** target (Chrome/Safari on macOS), **no SEO**, **no real-time/websockets**, **no authentication**, **structured log output** for CLI progress, **config via typed Pydantic settings + env vars**.

### Technical Architecture Considerations

- **CLI:** new ETF asset-class handling in the existing FirstRate import command group (`src/cli/`). Scriptable, non-interactive; structured progress logging to terminal (Phase 1 pattern). No shell completion needed.
- **Config:** new `FMPSettings` (API key, base URL, rate-limit params) nested under `Settings`, mirroring `IBKRSettings`/`KrakenSettings`. `FIRSTRATE_CATALOG_PATH` reused. Never hardcoded.
- **Metadata loader:** a new service (e.g. `FMPMetadataLoader` / `InstrumentMetadataService`) with lazy client init + persistent cache, following the `DataCatalogService` lazy-init + availability-caching pattern. Built provider-agnostic at the interface so future asset classes reuse it.
- **Metadata persistence:** resolved ETF metadata is stored so it survives re-runs and feeds both backtest instrument qualification and explorer display.
- **Web:** new/extended routes in `src/api/ui/` (explorer) and `src/api/rest/` (chart/metadata endpoints) via the existing `get_db() → repository → service` DI chain and `NavigationState` context. 30min added to the timeframe enum/selector. `@computed_field` for `N/A`-aware display props.
- **Backtest:** ETF catalog routed to BacktestEngine via the existing `BacktestOrchestrator` path; whole-share equity sizing reused. Venue-qualified `InstrumentId` is the precondition.

### Manual Venue-Override Mechanism (decided)

- **Mechanism: `venue_overrides.csv`** (Option B). When FMP cannot resolve a venue, the ticker is written to an unresolved-venue report. The operator fills in correct venues in a `venue_overrides.csv` (ticker → venue, bulk-editable in a spreadsheet, git-trackable).
- Re-running the import (or a metadata refresh) **merges `venue_overrides.csv` into the metadata store**, taking precedence over (absent) FMP venue data.
- No web UI write-path — the explorer remains read-only (preserves the Phase 1 stance).
- The completion gate checks the merged result: **0 tickers missing venue** across FMP-resolved + override-resolved.

### Implementation Considerations

- **ZIP extraction** before parsing (26 letter-batched archives × 5 timeframes), leaving catalog consistent on interruption.
- **`FirstRateCsvParser` reused** unchanged for the 6-column `.txt` schema.
- **`N/A` handling** threaded through instrument mapping, explorer presentation models, and backtest config — no consumer assumes metadata is always populated.
- **FMP cache** keyed by ticker; re-runs hit cache, not the API (quota protection).
- **Unresolved-venue report** as CLI output + a persisted list the operator works through into `venue_overrides.csv`.

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**MVP Approach:** Problem-solving MVP — the whole of Phase 2 is one indivisible vertical slice. The gating problem is ETF instrument metadata; solving it (FMP loader + venue gate) is what unlocks everything else. The slice is "done" only when ETFs import, qualify, render, and backtest.

**Resource model:** Solo developer. Epics are sequenced so each lands a usable increment; the system remains fully functional for Stocks throughout. Pause-and-resume safe at any epic boundary.

### MVP Feature Set (Phase 2 — proposed epic breakdown)

**Core User Journeys Supported:** All five (ETF Import, Venue Gap Resolution, Multi-ETF Backtest, Explorer Verification, Recovery & Re-run).

- **E1 — FMP Metadata Loader (foundation, reusable):** `FMPSettings` config; rate-limit-aware client; provider-agnostic `InstrumentMetadataService` interface; persistent cache; `N/A` sentinel for descriptive gaps; graceful degradation. *This is the keystone — built first, generalized for later asset classes.*
- **E2 — ETF Import Pipeline:** ZIP extraction (26 archives × 5 timeframes); reuse `FirstRateCsvParser`; Parquet catalog write under `FIRSTRATE_CATALOG_PATH`; all 5 timeframes incl. 30min; dry-run; row-count + OHLC + sample-point verification; idempotent date-range re-runs; import summary report.
- **E3 — Venue Resolution & Completeness Gate:** unresolved-venue report; `venue_overrides.csv` merge (precedence over absent FMP venue); coverage check enforcing **0 missing venues**; non-backtestable flagging for unresolved tickers.
- **E4 — Explorer ETF Support:** ETF tickers in the catalog browser; 5-timeframe chart display incl. 30min; metadata panel with `N/A`-aware rendering; explicit accuracy-verification task (bars + metadata vs TradingView).
- **E5 — Backtest Integration & Verification:** ETF catalog → BacktestEngine via `BacktestOrchestrator`; whole-share equity sizing; multi-ETF backtest produces a results record; reference-consistency check vs existing loader path.

### Post-MVP Features

Deferred Growth and Vision items are enumerated in **[Product Scope](#product-scope)** above. The Phase-2-specific note: epic **E1 (FMP Metadata Loader)** is deliberately built provider-agnostic and reusable, so applying it to the next asset class (Futures / FX / Crypto / Indices / Delisted) is incremental, not a rebuild.

### Risk Mitigation Strategy

| Risk | Impact | Mitigation |
|---|---|---|
| FMP venue coverage gaps larger than expected (manual effort balloons) | Venue gate stalls Phase 2 completion | `venue_overrides.csv` bulk workflow; completion gate makes remaining count explicit and trackable; descriptive gaps tolerated as `N/A` so only venue blocks |
| FMP returns *wrong* (not just missing) metadata | Silent wrong-venue backtests | Spot-check verification against known large ETFs (SPY/QQQ/IWM…); explorer accuracy task; no auto-trust of low-confidence resolutions |
| FMP rate-limit / quota exhaustion across ~5,039 tickers | Import throttled or blocked | Rate-limit-aware throttling; persistent cache (re-runs don't re-fetch); resolution is one-time then cached |
| FMP API/network failure mid-import | Partial metadata, aborted run | Graceful degradation to `N/A` / unresolved queue; never abort whole import; idempotent resume |
| 30min timeframe expands catalog convention (4→5) | Inconsistency between stock and ETF catalogs | ETFs at 5 from the start; stock 30min backfill explicitly deferred + tracked; timeframe enum updated centrally |
| ZIP extraction disk/space pressure | Import fails midway | Dry-run disk estimate; extract/parse one ticker at a time (bounded); idempotent re-run after freeing space |
| Solo developer — effort stalls | Phase 2 incomplete | Epics independently shippable; Stocks remain fully functional; resume at any epic boundary |

*(Market risk: N/A — personal single-operator tool.)*

## Functional Requirements

### Instrument Metadata Resolution (E1)

- **FR1:** System can resolve instrument metadata (venue, currency, asset type, name, sector, country, IPO date) for a given ticker from an external provider (FMP).
- **FR2:** System can persist resolved metadata so subsequent runs reuse it without re-querying the provider.
- **FR3:** System can record an explicit `N/A` value for any descriptive metadata field the provider cannot supply, distinguishable from "not yet resolved."
- **FR4:** System can continue processing remaining tickers when metadata resolution for one ticker fails or returns partial data.
- **FR5:** System can expose its metadata-resolution capability through a provider-agnostic interface reusable by asset classes beyond ETFs.
- **FR6:** System can report metadata-resolution outcomes per import (count resolved automatically, count with descriptive gaps, count with unresolved venue).

### ETF Data Import (E2)

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

### Venue Resolution & Completeness Gate (E3)

- **FR20:** System can map ETF tickers to Nautilus-qualified instrument IDs using resolved venue metadata.
- **FR21:** System can produce an unresolved-venue report listing every ticker lacking a venue, with the reason.
- **FR22:** Operator can supply manual venue corrections that take precedence over (absent) provider data.
- **FR23:** System can merge manual venue corrections into the metadata store on re-run/refresh.
- **FR24:** System can report current venue coverage and identify whether any ticker in the backtestable universe lacks a venue.
- **FR25:** System can exclude tickers without a resolved venue from the backtestable universe and flag them, without dropping their imported bars.
- **FR26:** System can refuse to mark the ETF universe complete while any ticker lacks a venue (no inferred/guessed venues).

### Data Explorer — ETF Support (E4)

- **FR27:** Operator can view imported ETF tickers in the catalog browser with available date ranges and timeframes.
- **FR28:** Operator can search/filter the ETF ticker list by symbol.
- **FR29:** Operator can load a windowed chart for a selected ETF at any of the five native timeframes (including 30min).
- **FR30:** Operator can view a selected ETF's metadata (name, venue, sector, etc.), with `N/A` rendered clearly where unavailable.
- **FR31:** Operator can view basic data statistics for a selected ETF (row count, date range, min/max prices per timeframe).
- **FR32:** Operator can verify ETF bar and metadata accuracy in the explorer against an external reference.

### Backtest Integration & Verification (E5)

- **FR33:** System can serve imported ETF catalog data to the Nautilus BacktestEngine as a data source.
- **FR34:** Operator can run a backtest against imported ETF data using an existing strategy.
- **FR35:** System can apply whole-share (equity-style) position sizing for ETF backtests.
- **FR36:** Operator can run a backtest spanning multiple ETFs.
- **FR37:** System can persist ETF backtest results to the results database, viewable in the web UI.
- **FR38:** Operator can compare ETF backtest results against a reference path to confirm consistency.

### Configuration (cross-cutting)

- **FR39:** Operator can supply the FMP API key and connection settings via typed environment-based configuration.
- **FR40:** Operator can configure the source ETF data directory and the isolated FirstRate catalog path via configuration.

## Non-Functional Requirements

### Performance

| Operation | Target | Rationale |
|---|---|---|
| Explorer chart load (single ETF/timeframe) | < 2 seconds | Responsive visual verification vs TradingView (Phase 1 target preserved) |
| Ticker list render (paginated) | < 500ms | Standard pagination across ~5,000 ETFs |
| Ticker search/filter | < 300ms | Keystroke-responsive filtering |
| Catalog metadata read | < 500ms | Enumerate tickers/timeframes without loading price data |
| Chart scroll/zoom | 60fps | Native to charting library's data conflation |
| Import throughput | No hard target | One-time/occasional batch; prioritize reliability over speed; one ticker at a time (bounded memory) |
| FMP metadata resolution | No hard target, but **rate-limit-bounded** | Throttled to provider quota; cached so cost is paid once, not per re-run |

### Reliability

- **Import fault tolerance:** Any single ticker (bar import *or* metadata resolution) can fail without blocking the rest; failures logged and surfaced in the summary.
- **Graceful interruption:** Interrupted imports leave the catalog consistent — no partially-written Parquet readable as valid by the backtest engine.
- **Idempotent recovery:** Re-running resumes via date-range comparison; the FMP cache prevents redundant re-fetching.
- **Metadata-store consistency:** Resolved metadata and manual venue overrides survive restarts and re-runs; the explorer never serves stale metadata after a refresh.
- **Completeness enforcement:** The venue-coverage check is authoritative — the system can always answer "is any ticker missing a venue?" deterministically.

### Integration (FMP — primary new surface)

- **Rate-limit compliance:** The FMP client throttles to stay within the subscription's request quota; bulk resolution across ~5,039 tickers never exceeds it.
- **Caching contract:** Resolved metadata is persisted and reused; a re-run resolves ≈0 already-cached tickers (near-100% cache hit).
- **Failure isolation:** FMP timeouts, errors, or partial responses degrade to `N/A` (descriptive) or the unresolved-venue queue (venue) — never abort the whole import.
- **Catalog compatibility:** Imported ETF Parquet is directly consumable by the Nautilus BacktestEngine with no runtime adapter, following the existing `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet` convention.
- **Existing-pattern conformance:** CLI commands follow the existing Click structure; explorer routes follow the existing FastAPI/HTMX DI chain and charting stack.

### Security

- **Minimal scope:** Personal localhost tool — no authentication, authorization, or encryption required on the UI/CLI.
- **Credential hygiene:** FMP API key supplied via typed settings / env var (`FMPSettings`), never hardcoded or committed. `.env` excluded from git (existing convention).
- **No regulatory burden:** KYC/AML, PCI-DSS, GDPR, SOX out of scope (personal tool).

### Maintainability (project standard)

- Conforms to existing standards: Python 3.11+ with type hints, ruff + mypy gates, file/function/class size limits, TDD with the established test pyramid (unit/component/integration `--forked`/e2e), >80% coverage on `src/core` + `src/strategies`.
