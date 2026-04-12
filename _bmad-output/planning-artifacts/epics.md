---
stepsCompleted:
  - 'step-01-validate-prerequisites'
  - 'step-02-design-epics'
  - 'step-03-create-stories'
  - 'step-04-final-validation'
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
  - 'ux-design-specification.md'
---

# Trading-ntrader - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for Trading-ntrader, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: User can trigger a data import for a specified asset class via CLI command
FR2: User can run a dry-run validation that scans source directories and reports file counts, detected schemas, date ranges, and estimated disk usage without writing any data
FR3: System can parse FirstRate Data's 6 distinct CSV schemas (stocks/ETFs 6-column, FX daily 6-column YYYYMMDD, FX 1-min 7-column split date/time, Futures daily 7-column with open interest, Futures 1-min 6-column, Index 5-column no-volume)
FR4: System can convert parsed CSV data into Nautilus-compatible Parquet catalog format
FR5: System can map FirstRate ticker symbols to Nautilus-qualified instrument IDs (e.g., SPY -> SPY.ARCA) using reference data (company_profiles.csv for stocks/ETFs, asset-specific mapping rules for futures/FX/crypto/indices)
FR6: System can validate OHLC data during import (high >= low, volume >= 0) and reject or flag invalid rows
FR7: System can verify import completeness by comparing row counts between source CSV and output Parquet per ticker/timeframe
FR8: System can validate import accuracy by comparing sample data points (first and last 10 rows) between source CSV and output Parquet
FR9: System can detect incomplete prior imports by comparing last date in existing Parquet against last date in source CSV, and re-import only incomplete tickers
FR10: System can produce an import summary report showing total tickers processed, rows imported, and any failures with reasons
FR11: System can report import progress to the CLI during execution (tickers processed, current ticker, errors encountered)
FR12: System can store imported Parquet data in an isolated catalog path configured via FIRSTRATE_CATALOG_PATH environment variable
FR13: System can handle sharded source directories (alphabetical subdirectories for stocks/ETFs, numbered archive shards for delisted stocks)
FR14: System can extract zip archives before parsing (delisted stock shards)
FR15: User can view a paginated, searchable list of all imported tickers with available date ranges and timeframes
FR16: User can search/filter the ticker list by ticker symbol name
FR17: User can select a ticker and view available timeframes for that ticker
FR18: User can load a windowed chart view for a selected ticker at a natively-stored timeframe (1-min, 5-min, 1-hour, daily)
FR19: User can scroll and zoom the chart to navigate through the full historical date range
FR20: User can view basic data statistics for a selected ticker (row count, date range, min/max prices per timeframe)
FR21: User can view supplementary data for a ticker when available (company profile, dividend history, stock split history)
FR22: System can serve imported FirstRate catalog data to the Nautilus BacktestEngine as a data source
FR23: System can route backtest data requests to the FirstRate catalog based on configuration
FR24: System can read catalog metadata (available tickers, date ranges, timeframes) without loading full price data
FR25: User can run a backtest against imported FirstRate data using an existing strategy
FR26: System can apply asset-appropriate position sizing during backtests (whole shares for equities/ETFs, contract lots for futures, lot-based for FX, fractional for crypto, index-appropriate for indices)
FR27: User can compare backtest results between FirstRate catalog data and existing CSV loader data on a reference dataset to verify consistency
FR28: System can parse company_profiles.csv (ticker, name, country, state, exchange, sector, industry, IPO date)
FR29: System can parse dividend history files (date, dividend amount per ticker)
FR30: System can parse stock split history files (date, split ratio per ticker)
FR31: System can associate supplementary data with the correct ticker in the explorer
FR32: User can specify the source data directory for import
FR33: User can specify the target asset class for import
FR34: User can specify which timeframes to import (one, several, or all available)

### NonFunctional Requirements

NFR1: Chart data load for a single ticker/timeframe must complete in under 2 seconds
NFR2: Ticker list page render (paginated) must complete in under 500ms
NFR3: Ticker search/filter must respond in under 300ms across 20,000+ tickers
NFR4: Explorer page initial load must complete in under 1 second
NFR5: Chart scroll/zoom interaction must run at 60fps (handled by charting library)
NFR6: Catalog metadata read must complete in under 500ms (enumerate tickers and timeframes without loading price data)
NFR7: Import performance has no target — prioritize simplicity and reliability; parsers process one ticker at a time (bounded memory)
NFR8: Import of any individual ticker can fail without blocking remaining tickers; failures logged and reported in summary
NFR9: Interrupted imports leave the catalog in a consistent state — no partially-written Parquet files readable as valid by the backtest engine
NFR10: Explorer serves correct data for any successfully imported ticker; no stale cache serving outdated metadata after a new import
NFR11: Imported Parquet files directly consumable by BacktestEngine without adapter layers or format conversion at runtime
NFR12: Output Parquet follows the same {INSTRUMENT_ID}/{BAR_TYPE}/*.parquet directory convention as IBKR/Kraken imports
NFR13: Explorer integrates with the existing web UI stack, dependency injection chain, and charting library
NFR14: Import commands follow the existing CLI framework and directory structure
NFR15: Configuration via environment variables or typed settings framework; no credentials in code or git
NFR16: All price data must maintain source decimal precision through CSV to Parquet conversion — no floating-point rounding artifacts
NFR17: CSV date formats must produce consistent UTC-normalized timestamps in Parquet output per asset class
NFR18: Zero data loss — row count parity between source CSV and output Parquet for every imported ticker/timeframe
NFR19: Catalog isolation — FirstRate data stored under FIRSTRATE_CATALOG_PATH, never mixed with IBKR/Kraken catalog data
NFR20: Idempotent operations — re-running an import command produces the same result; no duplicate data or state corruption

### Additional Requirements

- ADR-1: Catalog metadata stored in PostgreSQL (catalog_instruments table) for fast paginated search, sorting, and both async/sync access
- ADR-2: Named catalogs defined via Pydantic settings (CatalogSettings), consistent with existing IBKRSettings/KrakenSettings pattern
- ADR-3: Single catalog_instruments table combining instrument identity (ticker, Nautilus ID, exchange, asset class, name, sector, industry, IPO date) with import metadata (catalog name, date range, bar counts per timeframe)
- ADR-4: Strategy pattern with parser registry — each CSV schema is a parser class with common interface; all parsers produce list[Bar] with shared validation hooks; registration via @register_parser decorator
- ADR-5: Idempotent import with metadata gatekeeper — metadata DB row is the gatekeeper; only tickers with verified metadata are visible to system; per-ticker loop: parse -> validate -> write_data -> verify row count -> upsert metadata -> log success
- ADR-6: Instrument ID mapping via PostgreSQL — company_profiles.csv loaded into catalog_instruments table on first import; subsequent imports look up instrument IDs from DB
- ADR-7: Direct Parquet reads for chart data — chart API reads via catalog.bars() with time-range filtering; no intermediate storage or pre-aggregation
- ADR-8: SQL search for explorer — ticker search via SQL ILIKE prefix matching with B-tree index on catalog_instruments.ticker; pagination, sorting, and asset class filtering via standard SQL clauses
- Alembic migration required for catalog_instruments table (005_add_catalog_instruments.py)
- Parser interface contract: parse_file(file_path, ticker) -> list[Bar], map_instrument_id(ticker, db_session) -> InstrumentId, validate_bars(bars) -> ValidationResult
- File organization: all FirstRate code under src/services/firstrate/ with parsers/, import_service.py, instrument_mapper.py, catalog_manager.py, metadata_service.py
- Database indexes: B-tree on ticker, composite on (catalog_name, asset_class), unique on (catalog_name, ticker)
- Dual repository pattern required: async for web, sync for CLI
- CLI command structure: ntrader import --format firstrate --catalog <name> <source-path> [--asset-class <type>] [--dry-run] [--timeframe <list>]; exit codes 0/1/2
- Chart API response format: JSON with UNIX second timestamps, float prices, wrapped in Pydantic response model
- HTMX fragments: no html/body tags, container ID matches hx-target, multi-target updates use hx-swap-oob
- Import pipeline must respect LogGuard lifecycle — no double initialization of C logging
- Nautilus multi-instrument backtest confirmed — BacktestDataConfig natively accepts list of instrument_ids
- Implementation sequence: DB schema -> Pydantic settings -> Parser framework -> Instrument mapping -> Import pipeline CLI -> Chart API -> Explorer UI

### UX Design Requirements

UX-DR1: Catalog selector dropdown at top of explorer page, scoping all content to selected catalog; persists selection in URL query param (?catalog=name); switching catalog triggers full content refresh
UX-DR2: Ticker search input with keystroke-responsive filtering (hx-trigger="keyup changed delay:300ms"); auto-focused on page load; clears on Escape key; sends search text + asset class filter + catalog as params
UX-DR3: Asset class filter pills — horizontal row of pill buttons (All, Stock, ETF, Futures, FX, Crypto, Index, Delisted) with ticker counts; single selection; filter state preserved in URL param (e.g. `?asset_class=stock`); filter changes reset pagination to page 1. **Zero-count behavior:** when no search filter is active, pills for asset classes with zero imported tickers are hidden (Phase 1 has Stocks only, so only "All" and "Stock" render by default); when a search filter is active, zero-count pills render with "0" so the user can see their query matched nothing in class X
UX-DR4: Ticker list table with inline metadata per row: Symbol (mono bold), Name, Asset class badge (colored per type), Date range (mono), Coverage bar (4px proportional fill), Bar counts "D / 1H / 1m" (mono compact); sortable by symbol, date range, bar count; 25 rows per page; clicking row loads chart + stats + supplementary via hx-swap-oob
UX-DR5: Timeframe button toolbar (1m, 5m, 1H, D) above chart; one-click switching via hx-get targeting chart panel fragment; active state highlighted (bg-blue-500); disabled state for unavailable timeframes (determined from `bar_count_minute`, `bar_count_5min`, `bar_count_hourly`, `bar_count_daily` on the `catalog_instruments` row); timeframe preserved in URL param (?tf=D)
UX-DR6: Full-width chart panel using TradingView Lightweight Charts v5.0 with progressive loading via time-range API slicing; no loading spinner overlay on chart; thin 2px animated loading bar at top during initial load; chart as hero element maximizing viewport
UX-DR7: Data statistics panel — grid of stat cards (Date Range, Daily Bars, 1-Hour Bars, 5-Min Bars, 1-Min Bars, Price Range, Nautilus ID); loads simultaneously with chart on ticker selection; font-mono for all numeric data; auto-fit grid columns
UX-DR8: Collapsible supplementary data sections via <details> elements for company profile, dividend history, and stock split history; collapsed by default; progressive detail without cluttering primary view
UX-DR9: "Run Backtest" link/button in chart header that navigates to existing backtest run page with pre-filled query params (?catalog=...&ticker=...&timeframe=...&start=...&end=...); primary action button style (bg-blue-500 text-white)
UX-DR10: Breadcrumb navigation showing Explorer > {catalog name} > {ticker symbol}; each segment is a clickable link; uses existing breadcrumbs.html partial with NavigationState
UX-DR11: Empty states — no ticker selected: chart/stats/supplementary hidden; no search results: "No tickers found for '{query}'"; empty catalog: full page message with import command example; chart data unavailable: inline message in chart area with disabled timeframe button
UX-DR12: Loading states — chart: thin progress bar, no spinner overlay, existing data stays visible during pan/zoom loads; ticker list: subtle loading bar, previous results stay visible until new arrive; fragments: skeleton animate-pulse placeholders
UX-DR13: Error feedback — chart load failure: error message in chart area itself (not toast); API failure: inline error in affected HTMX fragment; network failure: retry once then "Connection error. Refresh to retry."
UX-DR14: All explorer state in URL for deep linking and bookmarkability — catalog, search, asset_class, ticker, timeframe, page, sort_by all as query params; browser back button works naturally
UX-DR15: Stacked full-width layout consistent with existing NTrader pages — filter bar, ticker list, chart, stats panel stacked vertically; each section an independent HTMX swap target; gap-4 between sections, p-4 panel padding, compact py-2 ticker rows
UX-DR16: Dark theme consistency — slate-950 background, slate-900 cards/panels, slate-800 hover/borders; asset class badge colors: Stock=blue (Phase 1 primary), ETF=slate, Futures=amber, FX=emerald, Crypto=purple, Index=cyan, Delisted=gray
UX-DR17: Keyboard accessibility — tab through search -> filter pills -> ticker rows -> timeframe buttons -> Run Backtest; visible focus ring (focus:ring-2 focus:ring-blue-500) on all interactive elements; semantic HTML (table, button, nav, details); ARIA labels on search input, filter pills (aria-pressed), selected row (aria-selected), chart region
UX-DR18: CLI import UX — streaming log lines (one per ticker with status); asset class headers separating batches; green checkmark per success, red X per failure; summary report at end with totals and failure table; no interactive prompts; exit codes 0/1/2

### FR Coverage Map

FR1: Epic 1 - CLI import command trigger
FR2: Epic 1 - Dry-run validation
FR3: Epic 1 - CSV schema parsing (6 schemas)
FR4: Epic 1 - CSV to Parquet conversion
FR5: Epic 1 - Ticker to Nautilus instrument ID mapping
FR6: Epic 1 - OHLC validation during import
FR7: Epic 1 - Row count verification (CSV vs Parquet)
FR8: Epic 1 - Sample point validation (first/last 10 rows)
FR9: Epic 1 - Idempotent re-import of incomplete tickers
FR10: Epic 1 - Import summary report
FR11: Epic 1 - CLI progress reporting
FR12: Epic 1 - Isolated catalog path via FIRSTRATE_CATALOG_PATH
FR13: Epic 1 - Sharded source directory handling
FR14: Dropped - Zip extraction handled manually before import
FR15: Epic 2 - Paginated searchable ticker list
FR16: Epic 2 - Ticker search/filter by symbol
FR17: Epic 2 - Ticker selection with timeframe display
FR18: Epic 2 - Windowed chart view at native timeframes
FR19: Epic 2 - Chart scroll/zoom navigation
FR20: Epic 2 - Data statistics display per ticker
FR21: Epic 4 - Supplementary data display (company, dividends, splits)
FR22: Epic 3 - Serve FirstRate catalog to BacktestEngine
FR23: Epic 3 - Route backtest data requests to FirstRate catalog
FR24: Epic 2 - Catalog metadata read without loading price data
FR25: Epic 3 - Run backtest against imported FirstRate data
FR26: Epic 3 - Asset-appropriate position sizing per asset class
FR27: Epic 3 - Reference comparison against existing CSV loader
FR28: Epic 1 - Parse company_profiles.csv for instrument mapping
FR29: Epic 4 - Parse dividend history files
FR30: Epic 4 - Parse stock split history files
FR31: Epic 4 - Associate supplementary data with tickers in explorer
FR32: Epic 1 - Specify source data directory
FR33: Epic 1 - Specify target asset class
FR34: Epic 1 - Specify timeframes to import

## Epic List

### Epic 1: Data Import Pipeline
User can import FirstRate Data Stocks CSV files into the Nautilus Parquet catalog via CLI, with dry-run validation, OHLC integrity checks, row count verification, sample point validation, progress reporting, and idempotent re-runs for interrupted imports. Includes the full foundation: database migration (catalog_instruments table), Pydantic settings (CatalogSettings), parser framework with strategy pattern and registry (shared `FirstRateCsvParser` registered for STOCK and ETF), instrument ID mapping from company_profiles.csv, and the Click CLI command with dry-run mode. **Phase 1 pivoted from ETF to Stocks (2026-04-11)** — Stocks ship with `company_profiles.csv` metadata; ETF metadata will be sourced separately in Phase 2 via a dedicated FMP-backed metadata loader story.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR8, FR9, FR10, FR11, FR12, FR13, FR28, FR32, FR33, FR34

### Epic 2: Data Explorer & Verification
User can browse all imported tickers in a paginated, searchable web UI with asset class filtering, load interactive candlestick charts at any stored timeframe using TradingView Lightweight Charts, and view data statistics — enabling side-by-side visual verification against TradingView to build trust in the imported data. Includes catalog selector, search input, filter pills, ticker list table, timeframe toolbar, chart panel, and statistics panel.
**FRs covered:** FR15, FR16, FR17, FR18, FR19, FR20, FR24

### Epic 3: Backtest Integration & Verification
User can run backtests against imported FirstRate catalog data using existing strategies, with asset-appropriate position sizing, and compare results against existing data sources to verify consistency. Includes the explorer-to-backtest bridge ("Run Backtest" button with pre-filled context) and catalog routing in the BacktestOrchestrator.
**FRs covered:** FR22, FR23, FR25, FR26, FR27

### Epic 4: Supplementary Data
User can view company profiles, dividend history, and stock split history alongside ticker data in the explorer via collapsible detail sections, enriching the data research workflow with fundamental context.
**FRs covered:** FR21, FR29, FR30, FR31

## Epic 1: Data Import Pipeline

User can import FirstRate Data Stocks CSV files into the Nautilus Parquet catalog via CLI, with dry-run validation, OHLC integrity checks, row count verification, sample point validation, progress reporting, and idempotent re-runs for interrupted imports. The same pipeline extends to ETFs (and other asset classes sharing the 6-column CSV schema) once their instrument metadata is available.

### Story 1.1: Catalog Foundation & Configuration

As a system operator,
I want named catalog configuration, a catalog_instruments database table, and domain models for the import pipeline,
So that the system has the foundation to manage multiple data catalogs and track imported instruments.

**Acceptance Criteria:**

**Given** the system has no catalog infrastructure
**When** the Alembic migration is run
**Then** a `catalog_instruments` table is created with columns: id (BigInteger PK), ticker, nautilus_id, asset_class, catalog_name, exchange, name, sector, industry, ipo_date, date_range_start, date_range_end, bar_count_daily, bar_count_hourly, bar_count_minute, created_at, updated_at
**And** indexes exist on ticker (B-tree), (catalog_name, asset_class) composite, and (catalog_name, ticker) unique constraint

**Given** the application configuration
**When** CatalogSettings is loaded from environment variables
**Then** named catalogs are defined with name, path, and format metadata consistent with existing IBKRSettings/KrakenSettings pattern
**And** FirstRateSettings includes source path defaults and catalog configuration

**Given** the domain model definitions
**When** domain models are imported
**Then** AssetClass enum, CatalogConfig, ValidationResult, and ImportResult Pydantic models are available in `src/models/catalog.py`

**Given** a named catalog is configured in settings
**When** CatalogManager resolves a catalog by name
**Then** it returns a ParquetDataCatalog instance initialized at the configured path

**Given** the catalog_instruments table exists
**When** MetadataService is used from the CLI (sync) or web (async)
**Then** it provides CRUD operations for catalog_instruments via dual repository pattern (sync for CLI, async for web)

### Story 1.2: ETF CSV Parser with OHLC Validation

As a system operator,
I want the system to parse FirstRate ETF headerless CSV files and validate OHLC data integrity,
So that I can trust the data before it enters the Parquet catalog.

**Acceptance Criteria:**

**Given** a FirstRate ETF CSV file with 6 headerless columns (Datetime, Open, High, Low, Close, Volume)
**When** the ETF parser processes the file
**Then** it produces a list of Nautilus Bar objects sorted by ts_init ascending
**And** all price values maintain source decimal precision (no floating-point rounding artifacts)
**And** timestamps are UTC-normalized

**Given** an ETF CSV file containing rows where high < low
**When** the parser validates the bars
**Then** the invalid rows are flagged in the ValidationResult with specific row details
**And** the validation reports the count and nature of invalid rows

**Given** an ETF CSV file containing rows where volume < 0
**When** the parser validates the bars
**Then** the invalid rows are flagged in the ValidationResult

**Given** the parser framework
**When** a new parser class is created
**Then** it extends the base parser ABC from `src/services/firstrate/parsers/base.py`
**And** it implements parse_file(file_path, ticker) -> list[Bar], map_instrument_id(ticker, db_session) -> InstrumentId, validate_bars(bars) -> ValidationResult
**And** it is registered via @register_parser(asset_class=AssetClass.ETF) decorator

**Given** the parser registry
**When** a parser is requested for asset class ETF
**Then** the registered ETF parser is returned
**And** requesting an unregistered asset class raises a clear error

### Story 1.3: Instrument ID Mapping from Company Profiles

As a system operator,
I want the system to map ETF ticker symbols to Nautilus-qualified instrument IDs using company_profiles.csv,
So that imported data has correct exchange-qualified identifiers for accurate backtesting.

**Acceptance Criteria:**

**Given** a company_profiles.csv file with columns (ticker, name, country, state, exchange, sector, industry, IPO date)
**When** the instrument mapper parses the file
**Then** all rows are loaded and available for instrument ID resolution
**And** the data is stored in the catalog_instruments table with exchange, name, sector, industry, and ipo_date fields populated

**Given** a ticker "SPY" with exchange "ARCA" in company_profiles.csv
**When** the instrument mapper resolves the Nautilus ID
**Then** it returns "SPY.ARCA" as the qualified instrument ID

**Given** a ticker that does not exist in company_profiles.csv
**When** the instrument mapper attempts resolution
**Then** it raises a clear error identifying the unmappable ticker
**And** the error does not block processing of other tickers

**Given** company_profiles.csv has already been loaded into the database for a catalog
**When** the instrument mapper is called again for the same catalog
**Then** it reads instrument IDs from the database instead of re-parsing the CSV
**And** subsequent lookups use the DB as the authoritative source

### Story 1.4: Import Pipeline Core

As a system operator,
I want the import service to orchestrate the full per-ticker import loop with verification,
So that ETF data flows from CSV files into the Nautilus Parquet catalog with verified integrity.

**Acceptance Criteria:**

**Given** a source directory containing ETF CSV files in alphabetical subdirectories (e.g., A/AAPL.txt, S/SPY.txt)
**When** the import service processes the directory for asset class ETF
**Then** it traverses all alphabetical subdirectories and discovers all ticker CSV files

**Given** a discovered ticker CSV file
**When** the import service processes it
**Then** it executes the per-ticker loop: parse CSV → validate OHLC → catalog.write_data(bars) → verify row count → verify sample points → upsert metadata → log success
**And** the Parquet output is written to the catalog path configured via FIRSTRATE_CATALOG_PATH

**Given** a successfully written Parquet file for a ticker
**When** row count verification runs
**Then** it confirms the source CSV row count matches the output Parquet row count exactly
**And** a mismatch is reported as a failure for that ticker

**Given** a successfully written Parquet file for a ticker
**When** sample point validation runs
**Then** it compares the first 10 and last 10 rows between source CSV and output Parquet
**And** values must match exactly (no precision loss)
**And** a mismatch is reported as a failure for that ticker

**Given** a ticker that fails at any step (parse, validate, write, verify)
**When** the failure occurs
**Then** the error is caught and logged with ticker name and reason
**And** the import continues to the next ticker without blocking
**And** no metadata row is upserted for the failed ticker (metadata gatekeeper pattern)

**Given** a ticker successfully completes the full import loop
**When** the metadata is upserted
**Then** the catalog_instruments row contains the ticker, nautilus_id, asset_class, catalog_name, date_range_start, date_range_end, and bar counts per imported timeframe

### Story 1.5: CLI Import Command with Progress & Summary

As a system operator,
I want a CLI command to trigger data import with streaming progress and a summary report,
So that I can import FirstRate data from the terminal and understand what happened.

**Acceptance Criteria:**

**Given** the CLI is available
**When** the user runs `ntrader import --format firstrate --catalog <name> <source-path>`
**Then** the import pipeline is triggered for the specified catalog and source directory

**Given** the import command
**When** the user specifies `--asset-class etf`
**Then** only ETF data is imported

**Given** the import command
**When** the user specifies `--timeframe daily,hourly`
**Then** only the specified timeframes are imported for each ticker

**Given** an import is in progress
**When** each ticker is processed
**Then** a structured log line is emitted with ticker name, timeframes, row count, and status (success/failure)
**And** progress is visible as streaming terminal output

**Given** the import completes (all tickers processed)
**When** the summary report is generated
**Then** it shows total tickers processed, total rows imported, count of successes, count of failures
**And** failures are listed with ticker name and failure reason
**And** the CLI exits with code 0 (all success), 1 (some failures), or 2 (fatal error)

**Given** no `--format` flag or an unsupported format
**When** the command is invoked
**Then** a clear error message is displayed and the CLI exits with code 2

### Story 1.6: Pre-Import Dry-Run Validation

As a system operator,
I want to run a dry-run that reports what would be imported without writing any data,
So that I can verify the source directory structure and estimate disk usage before committing.

**Acceptance Criteria:**

**Given** a source directory with FirstRate Stocks data (the Phase 1 target)
**When** the user runs `ntrader import --format firstrate --catalog <name> <source-path> --dry-run`
**Then** the system scans the directory structure without writing any data to Parquet or the database

**Given** a dry-run scan completes
**When** the results are reported
**Then** the output includes: detected asset classes, ticker count per asset class, available timeframes per asset class, total file count, and estimated disk usage for Parquet output

**Given** a source directory with files that don't match the expected FirstRate 6-column headerless CSV schema
**When** the dry-run detects schema mismatches
**Then** the mismatches are reported with file names and detected vs expected column counts

**Given** a dry-run scan
**When** it completes
**Then** no Parquet files are written, no database rows are created or modified, and the CLI exits with code 0

### Story 1.7: Idempotent Import & Failed Import Recovery

As a system operator,
I want the import to skip complete tickers and re-import only incomplete ones,
So that I can recover from interrupted imports by re-running the same command.

**Acceptance Criteria:**

**Given** a ticker that was fully imported in a prior run (metadata row exists, last date in Parquet matches last date in source CSV)
**When** the import command is re-run for the same catalog and source directory
**Then** the ticker is skipped with a log indicating it was already complete

**Given** a ticker that was partially imported (metadata row exists but last date in Parquet is earlier than last date in source CSV)
**When** the import command is re-run
**Then** the ticker is re-imported from scratch (full re-parse and re-write)
**And** the metadata row is updated with the new date range and bar counts

**Given** a ticker that has a Parquet file but no metadata row (orphaned from a crash before metadata upsert)
**When** the import command is re-run
**Then** the ticker is treated as new and fully imported (Parquet overwritten, metadata created)

**Given** an import that was interrupted and then resumed
**When** the summary report is generated
**Then** it distinguishes between skipped tickers (already complete), re-imported tickers (incomplete), and new tickers (first import)
**And** only re-imported and new tickers are counted in the "processed" total

## Epic 2: Data Explorer & Verification

User can browse all imported tickers in a paginated, searchable web UI with asset class filtering, load interactive candlestick charts at any stored timeframe using TradingView Lightweight Charts, and view data statistics — enabling side-by-side visual verification against TradingView to build trust in the imported data.

### Story 2.1: Explorer Page with Ticker List

As a system operator,
I want a web-based explorer page where I can browse, search, and filter all imported tickers with inline metadata,
So that I can quickly answer "what data do I have?" across my catalogs.

**Acceptance Criteria:**

**Given** the explorer page is loaded at `/explorer`
**When** the page renders
**Then** it displays a catalog selector dropdown, a search input (auto-focused), asset class filter pills with ticker counts, and a paginated ticker list
**And** the page extends base.html and follows existing NavigationState pattern with "Explorer" added to the navigation bar

**Given** a catalog with imported tickers
**When** the ticker list renders
**Then** each row displays: ticker symbol (font-mono bold), name, asset class badge (colored per type), date range (font-mono), coverage bar, and bar counts "D / 1H / 1m" (font-mono compact)
**And** the list is paginated at 25 rows per page with sort headers (symbol, date range, bar count)

**Given** the ticker list is displayed
**When** the user types "SP" in the search input
**Then** the ticker list filters in real-time via HTMX partial swap (debounced at 300ms) to show only tickers with prefix matching "SP" (case insensitive)
**And** pagination resets to page 1
**And** the search text is preserved in the URL query param (?search=SP)

**Given** asset class filter pills are displayed
**When** the user clicks an asset class pill (e.g. "Stock" in Phase 1)
**Then** the ticker list filters to show only tickers of that asset class via HTMX partial swap
**And** the active pill shows active state (bg-blue-500)
**And** filter state is preserved in URL param (e.g. `?asset_class=stock`)
**And** search and filter combine: selecting "Stock" + typing "SP" shows only Stocks matching "SP"

**Given** the catalog selector dropdown
**When** the user switches to a different catalog
**Then** the full explorer content refreshes (ticker list, stats, chart cleared) scoped to the new catalog
**And** the catalog selection is preserved in URL param (?catalog=name)

**Given** the explorer page URL contains query params (catalog, search, asset_class, page, sort_by)
**When** the page is loaded or refreshed
**Then** the view restores the exact state from the URL (deep linking / bookmarkability)

**Given** breadcrumb navigation
**When** a catalog is selected
**Then** breadcrumbs show Explorer > {catalog name}
**And** each segment is a clickable link using existing breadcrumbs.html partial

**Given** the REST endpoint `GET /api/explorer/tickers`
**When** called with query params (catalog, search, asset_class, page, sort_by)
**Then** it returns a paginated TickerListResponse (Pydantic model) with ticker metadata from the catalog_instruments table
**And** search uses SQL ILIKE prefix matching on the indexed ticker column
**And** response time is under 500ms for paginated results and under 300ms for search filtering

### Story 2.2: Chart Panel with Timeframe Switching

As a system operator,
I want to load an interactive candlestick chart for any ticker at any stored timeframe,
So that I can visually verify imported data against TradingView for trust in data quality.

**Acceptance Criteria:**

**Given** the ticker list is displayed
**When** the user clicks a ticker row
**Then** a chart panel loads below the ticker list with the daily timeframe as default
**And** the chart, stats, and supplementary panels update simultaneously via hx-swap-oob
**And** the selected ticker row is highlighted (bg-slate-800/blue-900)
**And** the URL updates with ?ticker={symbol}&tf=D

**Given** a chart is displayed for a ticker
**When** a timeframe toolbar is shown above the chart with buttons (1m, 5m, 1H, D)
**Then** the active timeframe button is highlighted (bg-blue-500 text-white)
**And** timeframe buttons for unavailable data are disabled (opacity-50 cursor-not-allowed)

**Given** a chart is displayed
**When** the user clicks a different timeframe button (e.g., 1H)
**Then** the chart panel swaps via hx-get with the new timeframe data
**And** the URL updates with ?tf=1H
**And** the active timeframe button updates

**Given** a chart is displayed
**When** the user scrolls or zooms the chart
**Then** TradingView Lightweight Charts handles pan/zoom natively at 60fps
**And** progressive loading fetches additional data via time-range API slicing as the user pans to earlier dates

**Given** the REST endpoint `GET /api/chart/catalog/{ticker}`
**When** called with query params (catalog, tf, start, end)
**Then** it resolves the ticker to a Nautilus instrument ID from the database
**And** reads windowed bar data via catalog.bars() with time-range filtering
**And** returns a ChartDataResponse (Pydantic model) with bars as JSON array of {time (UNIX seconds), open, high, low, close, volume}, instrument_id, timeframe, and bar_count
**And** response time is under 2 seconds for any single ticker/timeframe combination

**Given** the chart panel template
**When** rendered
**Then** the chart is full-width, maximizing viewport for visual verification
**And** a thin 2px animated loading bar appears at the top during initial chart load (no spinner overlay)

### Story 2.3: Data Statistics Panel

As a system operator,
I want to see data statistics for a selected ticker alongside the chart,
So that I can verify date ranges, bar counts, and price ranges match expectations.

**Acceptance Criteria:**

**Given** a ticker is selected in the explorer
**When** the stats panel loads (simultaneously with the chart)
**Then** it displays a grid of stat cards: Date Range, Daily Bars, 1-Hour Bars, 5-Min Bars, 1-Min Bars, Price Range, Nautilus ID
**And** the grid uses auto-fit columns that reflow based on viewport width

**Given** the stats panel is displayed
**When** the data is rendered
**Then** all numeric values use font-mono for alignment
**And** stat card labels use text-xs text-slate-500 uppercase
**And** stat card values use font-mono text-lg font-semibold

**Given** the stats panel
**When** the timeframe is changed
**Then** the price range stat updates to reflect the min/max prices for the selected timeframe

**Given** no ticker is selected
**When** the explorer page is loaded
**Then** the stats panel is hidden (not shown with placeholder text)

**Given** the REST endpoint `GET /api/explorer/ticker/{ticker}/stats`
**When** called with catalog query param
**Then** it returns a TickerStatsResponse (Pydantic model) with date_range_start, date_range_end, bar counts per timeframe, min/max prices per timeframe, and nautilus_id

### Story 2.4: Explorer UX Polish

As a system operator,
I want consistent loading states, clear empty states, informative error feedback, and keyboard accessibility in the explorer,
So that the explorer feels responsive, trustworthy, and usable without a mouse.

**Acceptance Criteria:**

**Given** the chart panel is loading initial data
**When** the request is in flight
**Then** a thin 2px animated loading bar appears at the top of the chart panel
**And** no spinner overlay blocks the chart area

**Given** the ticker list is updating (search/filter/pagination)
**When** the HTMX request is in flight
**Then** a subtle loading bar appears at the top of the ticker table via hx-indicator
**And** the previous results remain visible until new results arrive (no flash of empty content)

**Given** the stats or supplementary panels are loading
**When** the HTMX request is in flight
**Then** skeleton-style placeholders (animate-pulse on bg-slate-800 rectangles) display until content arrives

**Given** no ticker is selected on the explorer page
**When** the page renders
**Then** the chart area, stats panel, and supplementary section are hidden — only the filter bar and ticker list are visible

**Given** a search query with no matching tickers
**When** the ticker list updates
**Then** it shows "No tickers found for '{query}'" centered in the table body
**And** asset class pill counts update to reflect the filtered state

**Given** a catalog with no imported data
**When** the explorer page loads for that catalog
**Then** a full-page message displays: "No data in catalog '{name}'. Run an import to get started."
**And** the import command example is shown

**Given** a timeframe with no data for the selected ticker
**When** the chart panel renders
**Then** the chart area shows "No {timeframe} data available for {ticker}"
**And** the corresponding timeframe button was shown as disabled before clicking

**Given** a chart data API request fails
**When** the error response arrives
**Then** an error message displays in the chart area itself (not a toast): "Failed to load chart data for {ticker}. {reason}"

**Given** a network failure during any HTMX request
**When** the request fails
**Then** HTMX retries once, then shows "Connection error. Refresh to retry." in the affected fragment

**Given** the explorer page
**When** the user navigates via keyboard only
**Then** tab order flows: search box → filter pills → ticker list rows → timeframe buttons → Run Backtest button
**And** all interactive elements show a visible focus ring (focus:ring-2 focus:ring-blue-500)
**And** semantic HTML is used throughout (table, button, nav, details)
**And** ARIA labels are present: search input (aria-label="Search tickers"), filter pills (aria-pressed), selected ticker row (aria-selected), chart region (aria-label="Price chart for {ticker}")

**Given** a ticker is selected and Epic 4 (Supplementary Data) has not yet shipped
**When** the explorer renders the ticker detail view
**Then** a small neutral note is shown in the stats panel area: "Dividends and stock splits available in Epic 4"
**And** the note links to the epic tracker or PRD phase section
**And** no broken / empty supplementary `<details>` sections are rendered
**Note:** This AC is removed when Story 4.2 lands and supplementary data display becomes live

## Epic 3: Backtest Integration & Verification

User can run backtests against imported FirstRate catalog data using existing strategies, with asset-appropriate position sizing, and compare results against existing data sources to verify consistency. Includes the explorer-to-backtest bridge and catalog routing in the BacktestOrchestrator.

### Story 3.1: Catalog Integration with BacktestEngine

As a system operator,
I want to use imported FirstRate catalog data as a data source for the Nautilus BacktestEngine,
So that I can run backtests against the imported historical data without adapter layers or format conversion.

**Acceptance Criteria:**

**Given** a named catalog with imported FirstRate data (e.g., "firstrate-research")
**When** a backtest is configured with that catalog as the data source
**Then** CatalogManager resolves the named catalog to a ParquetDataCatalog instance at the configured path
**And** BacktestDataConfig is populated with the correct catalog path and instrument IDs

**Given** a backtest targeting a FirstRate catalog
**When** the BacktestOrchestrator sets up the engine
**Then** it routes data requests to the FirstRate ParquetDataCatalog based on the catalog configuration
**And** the imported Parquet files are directly consumed by BacktestEngine without any adapter layer or runtime format conversion

**Given** a backtest targeting multiple instruments from the FirstRate catalog (e.g., SPY, QQQ, IWM)
**When** the BacktestDataConfig is built
**Then** it passes multiple instrument IDs in a single config (leveraging Nautilus native multi-instrument support)
**And** the engine processes all instruments in a single run

**Given** a backtest request specifying a catalog that does not exist in settings
**When** the BacktestOrchestrator attempts to resolve it
**Then** a clear error is raised identifying the unknown catalog name
**And** the error does not crash the application

**Given** a backtest request specifying a ticker not present in the catalog
**When** the engine attempts to load data
**Then** the error is reported with the ticker name and catalog, identifying the missing data

### Story 3.2: Explorer-to-Backtest Bridge

As a system operator,
I want a "Run Backtest" button in the explorer that takes me to the backtest run page with pre-filled context,
So that I can seamlessly transition from verifying data to running a backtest without re-entering ticker details.

**Acceptance Criteria:**

**Given** a ticker is selected in the explorer with a chart displayed
**When** the chart header renders
**Then** a "Run Backtest" button is displayed with primary action styling (bg-blue-500 text-white)

**Given** the "Run Backtest" button is clicked
**When** navigation occurs
**Then** the browser navigates to the existing backtest run page with query params: ?catalog={catalog}&ticker={ticker}&timeframe={timeframe}&start={date_range_start}&end={date_range_end}

**Given** the backtest run page receives pre-filled query params from the explorer
**When** the page renders
**Then** the catalog, ticker, timeframe, and date range fields are pre-populated with the values from the URL
**And** the user can still modify any field before running the backtest

**Given** no ticker is selected in the explorer
**When** the chart area is hidden
**Then** no "Run Backtest" button is displayed

**Given** a backtest has completed and the user wants to return to the explorer
**When** navigation is available
**Then** a "Back to Explorer" link preserves the last explorer state (catalog, search, filter) via URL params

### Story 3.3: Backtest Verification & Reference Comparison

As a system operator,
I want to run an end-to-end backtest against FirstRate data and compare results with existing data sources,
So that I can verify the imported data produces consistent, trustworthy backtest results.

**Acceptance Criteria:**

**Given** imported FirstRate Stocks data in the catalog (e.g., AAPL daily)
**When** a backtest is run using an existing strategy with standard equity position sizing (whole shares)
**Then** the backtest completes successfully with results stored in the database
**And** results are viewable in the existing web UI backtest detail page

**Given** a backtest against FirstRate data
**When** position sizing is applied
**Then** the system uses asset-appropriate sizing: whole shares for equities/ETFs
**And** the architecture supports future asset-specific sizing (contract lots for futures, lot-based for FX, fractional for crypto) without structural changes

**Given** a reference dataset available from both the FirstRate catalog and the existing CSV loader (e.g., 1-year SPY daily)
**When** backtests are run against both data sources using the same strategy and parameters
**Then** the results are comparable: trade counts, entry/exit prices, and total PnL align within acceptable tolerance
**And** any discrepancies are explainable by known differences (e.g., adjustment methodology, data source timing)

**Given** a backtest against FirstRate data fails
**When** the error occurs
**Then** the error message identifies whether the issue is data-related (missing bars, wrong instrument ID) or engine-related (configuration, strategy error)
**And** the error is surfaced clearly in the web UI or CLI output

## Epic 4: Supplementary Data

User can view company profiles, dividend history, and stock split history alongside ticker data in the explorer via collapsible detail sections, enriching the data research workflow with fundamental context.

### Story 4.1: Dividend & Stock Split Data Parsing

As a system operator,
I want the system to parse FirstRate dividend and stock split history files and store them in the database,
So that supplementary data is available for display alongside ticker data in the explorer.

**Acceptance Criteria:**

**Given** a FirstRate dividend history file for a ticker (columns: date, dividend amount)
**When** the dividend parser processes the file
**Then** all dividend records are stored in the database associated with the correct ticker and catalog
**And** date values are parsed correctly and amounts maintain source decimal precision

**Given** a FirstRate stock split history file for a ticker (columns: date, split ratio)
**When** the split parser processes the file
**Then** all split records are stored in the database associated with the correct ticker and catalog
**And** split ratios are stored accurately (e.g., "2:1", "3:1", "1:4")

**Given** supplementary data files are available during an import
**When** the import pipeline processes a ticker
**Then** dividend and split data for that ticker is parsed and stored alongside the bar data import
**And** supplementary data parsing failures do not block the main bar data import for that ticker

**Given** a ticker that has no dividend or split history files
**When** the import processes that ticker
**Then** the import completes successfully with no supplementary data stored
**And** the explorer will show "No dividend history available" / "No split history available" for that ticker

**Given** an import is re-run for a ticker with existing supplementary data
**When** the supplementary data is processed
**Then** existing records are replaced with the latest parsed data (idempotent)

### Story 4.2: Supplementary Data Display in Explorer

As a system operator,
I want to see company profile, dividend history, and stock split history in the explorer when viewing a ticker,
So that I have fundamental context alongside price data for research and verification.

**Acceptance Criteria:**

**Given** a ticker is selected in the explorer
**When** the supplementary panel loads below the stats panel
**Then** it displays three collapsible `<details>` sections: Company Profile, Dividend History, Stock Split History
**And** all sections are collapsed by default

**Given** the Company Profile section is expanded
**When** the content renders
**Then** it displays: ticker, name, exchange, sector, industry, IPO date, country, and state
**And** data is sourced from the catalog_instruments table (populated during import from company_profiles.csv)

**Given** the Dividend History section is expanded
**When** the content renders
**Then** it displays a chronological list of dividend records (date and amount) for the selected ticker
**And** dates use a consistent format and amounts use font-mono

**Given** the Stock Split History section is expanded
**When** the content renders
**Then** it displays a chronological list of split records (date and ratio) for the selected ticker

**Given** a ticker with no dividend or split data
**When** the supplementary panel renders
**Then** the Dividend History section shows "No dividend history available"
**And** the Stock Split History section shows "No split history available"
**And** the Company Profile section still displays if company data exists

**Given** the supplementary panel
**When** loaded via HTMX fragment (`GET /explorer/supplementary?catalog=...&ticker=...`)
**Then** it loads as part of the hx-swap-oob response when a ticker is selected
**And** the REST endpoint `GET /api/explorer/ticker/{ticker}/stats` includes supplementary data availability flags
