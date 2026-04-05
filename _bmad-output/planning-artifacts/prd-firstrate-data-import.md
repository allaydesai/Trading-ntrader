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
  - 'product-brief-Trading-ntrader-distillate.md'
  - 'project-context.md'
  - 'product-brief-Trading-ntrader.md'
workflowType: 'prd'
documentCounts:
  briefs: 2
  research: 0
  brainstorming: 0
  projectDocs: 1
classification:
  projectType: 'Web App + CLI'
  domain: 'Fintech (algorithmic trading / quantitative analysis)'
  complexity: 'high'
  projectContext: 'brownfield'
---

# Product Requirements Document - Trading-ntrader

**Author:** Allay
**Date:** 2026-04-04

## Executive Summary

NTrader is a personal algorithmic trading backtester built on Nautilus Trader with IBKR and Kraken data sources, a PostgreSQL results database, and a FastAPI/HTMX web UI. The current system requires downloading market data from brokers on demand — a bottleneck when running large-scale backtests, parameter optimization sweeps, or walk-forward analysis across multiple instruments and timeframes.

This PRD defines a bulk historical data import pipeline and lightweight data explorer for FirstRate Data — an institutional-grade provider covering 20,934 tickers across 7 asset classes (~387GB+ of professionally cleaned CSV data). The pipeline converts FirstRate's headerless CSV files (6 distinct schemas across stocks, ETFs, futures, FX, crypto, and indices) into NTrader's existing Parquet catalog format for direct consumption by the Nautilus backtest engine. Phase 1 targets ETFs (5,072 tickers) as the simplest and most well-understood asset class.

The primary user is the system owner (sole operator) who needs to test strategies across large timeframes and diverse asset classes with confidence in data quality. No multi-user, authentication, or community-facing features are in scope.

### What Makes This Special

The core value is **backtesting breadth and depth without broker download friction**. FirstRate Data provides split+dividend adjusted, gap-checked historical bars that eliminate the download-and-wait cycle and remove data quality as a variable in backtest results. No existing NautilusTrader integration exists for FirstRate Data — competing providers like Databento and Tardis have native adapters, but FirstRate (with its lower price point and broad asset class coverage) does not.

The data explorer is deliberately minimal — a catalog browser and verification tool, not a charting platform. Its purpose: select a ticker, see available date ranges and data statistics, load a chart at natively-stored timeframes, and sanity-check against TradingView. Future iterations may surface metadata and fundamentals. The import pipeline is the product; the explorer builds confidence in the data.

## Project Classification

- **Type:** Web App + CLI (data import pipeline with lightweight explorer UI)
- **Domain:** Fintech — algorithmic trading / quantitative analysis
- **Complexity:** High — 6 CSV schemas, Nautilus instrument ID mapping, 387GB+ scale with idempotent imports, C extension isolation constraints
- **Context:** Brownfield — extending an established backtesting system (13+ shipped feature specs) with a new data source and catalog browsing capability

## Success Criteria

### User Success

- **Primary success moment:** Kick off a 10-year multi-ETF walk-forward analysis using FirstRate catalog data and it just works — no manual data wrangling, no broker download waits, no data quality doubts
- **Feature complete moment:** All 7 asset classes imported, verified, and backtestable — strategies can be tested across stocks, ETFs, futures, FX, crypto, indices, and delisted stocks from a single trusted catalog with appropriate position sizing and risk management per asset type
- Catalog browser shows available tickers with date ranges, letting the user quickly answer "what do I have?" before configuring a backtest
- Explorer chart loads at any natively-stored timeframe (1-min, 5-min, 1-hour, daily) and visually matches TradingView for the same ticker/period — confirming data integrity

### Business Success

- **3-month:** All ETFs (5,072 tickers) imported across all 4 timeframes, verified, and backtestable. At least one multi-year, multi-ETF walk-forward analysis completed successfully using exclusively FirstRate catalog data
- **6-month:** At least 3 additional asset classes imported and backtest-verified following the pipeline pattern established in Phase 1
- **12-month:** All 7 asset classes imported, backtest-verified with asset-appropriate position sizing and risk management. FirstRate catalog is the default data source for backtesting. Feature is complete
- Import pipeline pattern proven repeatable: adding a new asset class requires only a schema-specific parser, not architectural changes

### Technical Success

- Import pipeline is idempotent — re-running the same command skips complete tickers and re-imports incomplete ones via date-range comparison
- Pre-import dry-run validates file counts, schemas, date ranges, and disk estimates before committing
- Import verification at conversion time: row count match (source CSV vs Parquet), OHLC sanity (high >= low, volume >= 0), sample point validation (first/last N rows compared between source and output)
- Import summary report: total tickers processed, rows imported, failures with reasons
- Imported Parquet data is directly consumable by Nautilus BacktestEngine without conversion or adaptation layers
- Backtest results from FirstRate catalog are consistent with results from the existing CSV loader path on a reference dataset (e.g., 1-year SPY)
- All 6 CSV schema variants handled correctly (including FX 7-column, Futures open interest, Index no-volume)
- End-to-end backtest verification per asset class: each imported asset type must successfully complete a backtest with position sizing and risk management adapted to that asset's characteristics (whole shares for equities/ETFs, contract lots for futures, lot-based for FX, fractional for crypto, index-appropriate for indices)

### Measurable Outcomes

- 20,934 tickers across 7 asset classes importable across 4 timeframes with zero manual intervention after initial configuration
- Explorer loads chart data in under 2 seconds for any single ticker/timeframe combination
- Import pipeline produces identical Parquet catalog structure to existing IBKR/Kraken paths — no special handling in downstream backtest code
- Zero data loss: row count verification on every imported file confirms source CSV and Parquet parity
- Each asset class verified with at least one successful backtest demonstrating correct position sizing and risk management for that asset type

## Product Scope & Phased Development

### MVP Strategy

**Approach:** Problem-solving MVP — prove the import pipeline works end-to-end for one asset class (ETFs) before committing to the remaining six. ETFs are the validation vehicle: simplest schema, well-understood instrument IDs, largest ticker count after stocks. Success here validates the architecture for all subsequent phases.

**Resource model:** Solo developer. Each phase is independently shippable — the system is functional for all previously imported asset classes regardless of which phase is in progress.

### Phase 1 — MVP (ETFs)

**User Journeys Supported:** First-Time Import, Data Verification, Multi-ETF Backtest, Failed Import Recovery

**Capabilities:**
- **Import pipeline:** Idempotent file conversion for ETF asset class, all 4 native timeframes (1-min, 5-min, 1-hour, daily), pre-import dry-run validation, OHLC sanity checks, row count verification, sample point validation, import summary report
- **CSV parsing:** ETF schema (6-column headerless: Datetime,O,H,L,C,Volume)
- **Instrument ID mapping:** ETF tickers → Nautilus-qualified IDs using company_profiles.csv exchange data
- **Catalog output:** Parquet files in existing catalog structure, isolated via `FIRSTRATE_CATALOG_PATH` env var
- **Data explorer:** Paginated/searchable ticker list, timeframe selection, windowed chart view, date range and basic statistics display
- **CLI command:** Import trigger with dry-run mode and progress reporting
- **Backtest verification:** End-to-end backtest with standard equity position sizing; reference comparison against existing CSV loader path

### Phase 2 — Stocks (7,794 tickers)

Same schema as ETFs — validates pipeline at largest scale. Backtest with whole-share position sizing.

### Phase 3 — Futures (132 symbols)

First non-equity schema: 7-column daily with open interest. Continuous contract handling, exchange mapping (ES → ES.CME). Backtest with contract-based position sizing and margin awareness.

### Phase 4 — FX (79 pairs)

Highest-risk parser: YYYYMMDD date format, 7-column 1-min schema with split date/time columns. Backtest with lot-based sizing and pip-based risk management.

### Phase 5 — Crypto & Indices

Two asset classes, two schema variants. Crypto (74 tickers): decimal volume, fractional position sizing. Indices (128 tickers): 5-column no-volume schema, index-appropriate position sizing for direct backtesting.

### Phase 6 — Delisted Stocks (6,887 tickers)

Zip archive extraction, ticker reuse disambiguation. Most complex mapping problem. Backtest with historical equity sizing, handling of delist events.

### Cross-Phase Work

- Supplementary data display in explorer (company profiles, dividends, splits)
- Post-import data quality reports (gap detection, outlier flagging)

### Deferred — Future Feature Spec

- **Incremental/delta imports:** Spec written based on Phase 1 implementation learnings. Merge strategy depends on Parquet partitioning and catalog structure decisions that will only be clear after Phase 1

### Vision (Beyond This Feature)

- Backtest trade signal overlay on explorer charts
- Cross-asset correlation views
- Custom universe construction (e.g., "S&P 500 as of 2010")
- IBKR/Kraken gap-filling for FirstRate catalog
- Metadata and fundamentals surfaced in explorer

## User Journeys

### Journey 1: First-Time Data Import (Happy Path)

**Allay, solo quant trader** — has purchased FirstRate Data ETF bundles (1-min, 5-min, 1-hour, daily) and wants to get them into the NTrader Parquet catalog so they're available for backtesting.

**Opening Scene:** Allay has downloaded the FirstRate ETF data to a local directory. Thousands of headerless CSV files organized in alphabetical subdirectories. The goal: get these into the Nautilus-compatible Parquet catalog without writing custom scripts.

**Rising Action:** Allay sets the `FIRSTRATE_CATALOG_PATH` env var, points the CLI at the source directory, and runs a dry-run first. The pre-import validation scans the directory structure, reports file counts per timeframe, validates schemas match expected ETF format, and estimates disk usage. Everything checks out. Allay kicks off the actual import. The CLI reports progress — tickers processed, rows converted, any files that failed validation. It runs for a while, but that's fine — it's a one-time activity.

**Climax:** Import completes. The summary report shows 5,072 tickers across 4 timeframes, row counts verified against source CSVs, zero failures. The data is in the catalog.

**Resolution:** Allay opens the data explorer, searches for SPY, selects daily timeframe — a windowed chart view loads with recent history and the ability to scroll back through 20+ years. The catalog browser shows a paginated, searchable list of all 5,072 ETFs with their available date ranges. The data is ready for backtesting.

### Journey 2: Data Verification Against TradingView

**Opening Scene:** Import is done, but before running any serious backtests, Allay wants confidence the data is accurate — not just structurally valid, but actually correct prices.

**Rising Action:** Allay opens the data explorer, searches for SPY, picks the daily timeframe, and loads the chart. Opens TradingView in another tab with the same ticker and date range. Compares visually — do the candles match? Checks a few specific dates (earnings days, flash crashes) where price action is distinctive and easy to verify. Repeats for a couple more tickers across different timeframes (QQQ 1-hour, IWM 1-min).

**Climax:** The charts match. OHLC values align with TradingView for every spot-check. The data statistics in the explorer (row counts, date range, min/max prices) all look reasonable.

**Resolution:** Allay trusts the data. No more second-guessing whether backtest results are skewed by bad inputs. Confidence established.

### Journey 3: Multi-ETF Backtest (Core Success Moment)

**Opening Scene:** Allay has a momentum strategy that's been tested on a handful of ETFs via IBKR data. Now wants to validate it across 50 ETFs over 10 years — the kind of test that was impractical when every ticker required a separate broker download.

**Rising Action:** Allay configures backtest runs targeting 50 ETFs from the FirstRate catalog, daily bars, 2014-2024. Selects the momentum strategy with standard equity position sizing. Whether this is a single multi-instrument backtest or 50 individual runs depends on Nautilus engine capabilities — either way, the data is already in the catalog, no download wait.

**Climax:** Backtests complete. Results across all 50 ETFs are available with proper position sizing. Walk-forward analysis splits cleanly across the 10-year window. Results are in the database, viewable in the web UI.

**Resolution:** Allay can now iterate — adjust parameters, swap strategies, expand the universe to 200 ETFs — all without waiting for data. The feedback loop between hypothesis and validation is minutes, not hours.

**Open Question:** Nautilus multi-instrument backtest support needs research during architecture phase. Design must support either a single multi-instrument engine run or batch of single-instrument runs with aggregated results.

### Journey 4: Failed Import Recovery

**Opening Scene:** Allay is importing the full stock dataset (7,794 tickers, 4 timeframes). Midway through, the laptop runs out of disk space.

**Rising Action:** Allay frees up disk space and re-runs the exact same import command. The importer checks each ticker's Parquet file against its source CSV — comparing the last date present in the catalog against the last date in the source file. Tickers with complete data are skipped. Tickers with partial data are re-imported.

**Climax:** The import resumes without re-processing fully-imported tickers. No checkpoint files to manage, no manual bookkeeping — just idempotent operations with date-range validation to detect incomplete imports.

**Resolution:** Import completes. The summary report covers only the newly imported and re-imported tickers. Simple, reliable, repeatable.

### Journey 5: Incremental Dataset Update (Deferred)

**Note:** Documented for future planning. Implementation deferred to a dedicated feature spec informed by Phase 1 learnings.

**Scenario:** Months after the initial import, Allay purchases an updated dataset from FirstRate with newer data extending the date ranges. The desired outcome is an import that detects existing catalog data, identifies new date ranges, and appends only the delta without re-importing full history.

**Deferral Rationale:** The merge strategy depends on Parquet partitioning, file structure, and how Nautilus reads catalog data — details that will only be clear after Phase 1.

### Journey Requirements Summary

| Journey | Key Capabilities Revealed |
|---|---|
| First-Time Import | CLI import command, dry-run validation, progress reporting, OHLC checks, row count verification, import summary |
| Data Verification | Data explorer UI, ticker search, paginated ticker list, timeframe selection, windowed chart rendering, date range display, basic data statistics |
| Multi-ETF Backtest | FirstRate catalog integration with BacktestEngine, instrument ID mapping, position sizing per asset type. **Open:** multi-instrument vs batch — research needed |
| Failed Import Recovery | Idempotent imports with date-range validation (last date comparison), re-import of incomplete tickers |
| Incremental Update | Deferred — future feature spec informed by import implementation details |

## Domain-Specific Requirements

### Data Integrity

- **Decimal precision:** All price data must maintain source precision through the CSV → Parquet conversion pipeline. No floating-point rounding artifacts. The existing codebase enforces decimal arithmetic for financial calculations — the import pipeline must not break this chain
- **Timestamp integrity:** CSV date formats vary by asset class (YYYY-MM-DD for most, YYYYMMDD for FX, split date/time columns for FX 1-min). Each parser must produce consistent UTC-normalized timestamps in Parquet output. Silent date parsing errors corrupt time series without obvious symptoms — sample point validation is the primary defense
- **Zero data loss:** Row count parity between source CSV and output Parquet for every imported ticker/timeframe. No silent row dropping
- **Catalog isolation:** FirstRate data stored under `FIRSTRATE_CATALOG_PATH`, never mixed with IBKR/Kraken catalog data. Prevents adjustment methodology contamination
- **Idempotent operations:** Re-running an import command produces the same result. No duplicate data, no state corruption from interrupted runs

### Instrument ID Correctness

- **Exchange mapping accuracy:** Incorrect venue qualification (e.g., AAPL.NYSE instead of AAPL.NASDAQ) silently produces wrong backtest results. company_profiles.csv is the authoritative source for stock/ETF exchange mapping. Futures, FX, crypto, and indices each need their own mapping rules defined per phase
- **Ticker collision risk:** Delisted stocks may reuse tickers that now belong to different companies. Phase 6 must address disambiguation — but the mapping framework established in Phase 1 must be extensible enough to support it

### Adjustment Consistency

- **Single adjustment type per catalog:** FirstRate data is exclusively split+dividend adjusted. Mixing with IBKR/Kraken data (which may be unadjusted) in a single backtest produces invalid results. Catalog isolation via `FIRSTRATE_CATALOG_PATH` enforces separation at the storage level — backtest configuration must respect this boundary

### Out of Scope (Personal Tool)

KYC/AML, PCI-DSS, regional regulatory compliance, audit trails, fraud prevention, multi-user data protection do not apply.

## Technical Requirements

### Architecture Considerations

- **Rendering pattern:** Server-rendered HTML with HTMX partial updates (existing pattern). Explorer pages follow the same `NavigationState` + `templates.TemplateResponse()` pattern as current UI routes
- **CLI import pipeline:** Click-based CLI command (existing `src/cli/` pattern). Progress reporting via structured log output to terminal. No web UI integration for import monitoring
- **Browser target:** Primary browser only (Chrome or Safari on macOS). No cross-browser compatibility work required

### Implementation Considerations

- **Explorer UI routes:** New routes in `src/api/ui/` following existing DI chain pattern (`get_db()` → repository → service)
- **Chart API endpoints:** New REST endpoints in `src/api/rest/` returning windowed time-series data (not full history). Payload format: JSON array of OHLCV objects compatible with TradingView Lightweight Charts
- **Ticker list API:** Paginated endpoint with search/filter support. Source: Parquet catalog metadata (available tickers, date ranges, row counts per timeframe)
- **CLI import command:** New Click command group in `src/cli/`. Subcommands for dry-run validation and actual import. Asset class specified as argument
- **Catalog integration:** `DataCatalogService` extended (or new `FirstRateCatalogService`) to read from `FIRSTRATE_CATALOG_PATH`. Must follow existing lazy init + availability caching pattern

## Functional Requirements

### Data Import Pipeline

- **FR1:** User can trigger a data import for a specified asset class via CLI command
- **FR2:** User can run a dry-run validation that scans source directories and reports file counts, detected schemas, date ranges, and estimated disk usage without writing any data
- **FR3:** System can parse FirstRate Data's 6 distinct CSV schemas (stocks/ETFs 6-column, FX daily 6-column YYYYMMDD, FX 1-min 7-column split date/time, Futures daily 7-column with open interest, Futures 1-min 6-column, Index 5-column no-volume)
- **FR4:** System can convert parsed CSV data into Nautilus-compatible Parquet catalog format
- **FR5:** System can map FirstRate ticker symbols to Nautilus-qualified instrument IDs (e.g., SPY → SPY.ARCA) using reference data (company_profiles.csv for stocks/ETFs, asset-specific mapping rules for futures/FX/crypto/indices)
- **FR6:** System can validate OHLC data during import (high >= low, volume >= 0) and reject or flag invalid rows
- **FR7:** System can verify import completeness by comparing row counts between source CSV and output Parquet per ticker/timeframe
- **FR8:** System can validate import accuracy by comparing sample data points (first/last N rows) between source CSV and output Parquet
- **FR9:** System can detect incomplete prior imports by comparing last date in existing Parquet against last date in source CSV, and re-import only incomplete tickers
- **FR10:** System can produce an import summary report showing total tickers processed, rows imported, and any failures with reasons
- **FR11:** System can report import progress to the CLI during execution (tickers processed, current ticker, errors encountered)
- **FR12:** System can store imported Parquet data in an isolated catalog path configured via `FIRSTRATE_CATALOG_PATH` environment variable
- **FR13:** System can handle sharded source directories (alphabetical subdirectories for stocks/ETFs, numbered archive shards for delisted stocks)
- **FR14:** System can extract zip archives before parsing (delisted stock shards)

### Data Explorer

- **FR15:** User can view a paginated, searchable list of all imported tickers with available date ranges and timeframes
- **FR16:** User can search/filter the ticker list by ticker symbol name
- **FR17:** User can select a ticker and view available timeframes for that ticker
- **FR18:** User can load a windowed chart view for a selected ticker at a natively-stored timeframe (1-min, 5-min, 1-hour, daily)
- **FR19:** User can scroll and zoom the chart to navigate through the full historical date range
- **FR20:** User can view basic data statistics for a selected ticker (row count, date range, min/max prices per timeframe)
- **FR21:** User can view supplementary data for a ticker when available (company profile, dividend history, stock split history)

### Catalog Integration

- **FR22:** System can serve imported FirstRate catalog data to the Nautilus BacktestEngine as a data source
- **FR23:** System can route backtest data requests to the FirstRate catalog based on configuration
- **FR24:** System can read catalog metadata (available tickers, date ranges, timeframes) without loading full price data

### Backtest Verification

- **FR25:** User can run a backtest against imported FirstRate data using an existing strategy
- **FR26:** System can apply asset-appropriate position sizing during backtests (whole shares for equities/ETFs, contract lots for futures, lot-based for FX, fractional for crypto, index-appropriate for indices)
- **FR27:** User can compare backtest results between FirstRate catalog data and existing CSV loader data on a reference dataset to verify consistency

### Supplementary Data Management

- **FR28:** System can parse company_profiles.csv (ticker, name, country, state, exchange, sector, industry, IPO date)
- **FR29:** System can parse dividend history files (date, dividend amount per ticker)
- **FR30:** System can parse stock split history files (date, split ratio per ticker)
- **FR31:** System can associate supplementary data with the correct ticker in the explorer

### Import Configuration

- **FR32:** User can specify the source data directory for import
- **FR33:** User can specify the target asset class for import
- **FR34:** User can specify which timeframes to import (one, several, or all available)

## Non-Functional Requirements

### Performance

| Operation | Target | Rationale |
|---|---|---|
| Chart data load (single ticker/timeframe) | < 2 seconds | Responsive visual verification against TradingView |
| Ticker list page render (paginated) | < 500ms | Standard pagination at page sizes of 25-50 |
| Ticker search/filter | < 300ms | Keystroke-responsive filtering across 20,000+ tickers |
| Explorer page initial load | < 1 second | Simple form with TradingView Lightweight Charts JS |
| Chart scroll/zoom interaction | 60fps | Handled natively by TradingView Lightweight Charts data conflation |
| Catalog metadata read | < 500ms | Enumerate available tickers and timeframes without loading price data |
| Import performance | No target | One-time activity — prioritize simplicity and reliability. Parsers process one ticker at a time (bounded memory) |

### Reliability

- **Import fault tolerance:** Import of any individual ticker can fail without blocking remaining tickers. Failures logged and reported in summary
- **Graceful interruption:** Interrupted imports leave the catalog in a consistent state �� no partially-written Parquet files readable as valid by the backtest engine
- **Explorer consistency:** Explorer serves correct data for any successfully imported ticker. No stale cache serving outdated metadata after a new import

### Integration

- **Nautilus BacktestEngine compatibility:** Imported Parquet files directly consumable by BacktestEngine without adapter layers or format conversion at runtime
- **Existing catalog structure:** Output Parquet follows the same `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet` directory convention as IBKR/Kraken imports
- **Existing web UI patterns:** Explorer uses the same FastAPI/HTMX/Jinja2 stack, DI chain, NavigationState, and TradingView Lightweight Charts integration
- **Existing CLI patterns:** Import commands use the same Click-based CLI structure in `src/cli/`

### Security

- **Minimal scope:** Personal localhost tool — no authentication, authorization, or encryption required
- **Env var protection:** Configuration via environment variables or Pydantic settings. No credentials in code or git

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Nautilus Catalog v2 schema changes (GitHub Issue #991) | Rework of Parquet writing layer | Abstract catalog writing behind an interface — parsers produce normalized data, writer handles format. Schema change = update writer only |
| Multi-instrument backtest support unknown | Architecture decision blocks Journey 3 | Research during Phase 1. Design for either single multi-instrument run or batch with aggregated results |
| FX date format silently misparsed (YYYYMMDD + 7-column 1-min) | Corrupted time series across 79 pairs | Defer FX to Phase 4 (pipeline proven by then). Schema-specific parsers with format validation; sample point checks against known reference dates |
| Exchange mapping wrong for subset of tickers | Backtest uses wrong instrument metadata | Validate mapped IDs against known subset (e.g., top 50 ETFs by AUM) during Phase 1 verification |
| Parquet precision loss during conversion | Subtle price errors compound over long backtests | Compare raw CSV values against Parquet output for sample rows; use appropriate Parquet decimal types |
| Partial import produces valid-looking but incomplete data | Backtest runs on truncated history without warning | Date-range validation on re-run (Journey 4); explorer shows actual date range per ticker |
| 387GB+ dataset exceeds disk/memory expectations | Import fails or system destabilizes | Pre-import dry-run estimates disk usage. Parsers process one ticker at a time (bounded memory). Import time is not a concern |
| Solo developer — phases stall or get deprioritized | Feature incomplete | Each phase is independently shippable. Pause-and-resume at any phase boundary |
