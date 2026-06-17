---
title: "Product Brief: NTrader Historical Data Import & Explorer"
status: "complete"
created: "2026-04-04"
updated: "2026-04-04"
inputs:
  - "User brain dump (FirstRate Data dataset specs, directory structure, CSV schemas)"
  - "_bmad-output/project-context.md"
  - "docs/product/PRODUCT_OVERVIEW.md"
  - "docs/product/PRD.md"
  - "docs/agent/data-pipeline.md"
  - "docs/agent/web-ui.md"
  - "docs/agent/architecture.md"
  - "docs/webui/NTrader-webui-specification.md"
  - "Web research: competitive landscape, FirstRate Data reputation, NautilusTrader ecosystem"
---

# Product Brief: NTrader Historical Data Import & Explorer

## Executive Summary

Backtesting strategies against deep historical data is the foundation of quantitative trading — but getting 387GB of institutional-quality market data into a usable, trustworthy format remains a manual, error-prone slog. NTrader's Historical Data Import & Explorer closes this gap by building a self-contained research environment: a robust import pipeline for FirstRate Data's comprehensive multi-asset dataset (7 asset classes, 20K+ tickers, 1-min through daily bars), an interactive data explorer for visual inspection and quality validation, and seamless backtest execution — all without leaving one application.

The system converts FirstRate Data's CSV files into NTrader's Parquet catalog format, maintains a pristine isolated catalog enforced by dedicated catalog paths (never contaminated by live feed data), and supports incremental batch updates. Three native timeframes are imported directly from the provider (1-min, 1-hour, daily), with 15-min resampled from 1-min data — using provider-native resolutions where available for maximum accuracy. The data explorer provides statistics, multi-timeframe charting, and progressive loading — enabling effortless visual exploration of any ticker across the full historical range. Critically, the dataset includes 6,887 delisted stocks, enabling survivorship-bias-free backtests that most personal setups never achieve.

The end state: pick a ticker, explore its history visually, select a timeframe, and run a backtest — a closed research loop from data to insight.

## The Problem

NTrader currently supports data ingestion from IBKR and Kraken APIs, but these live feeds have significant limitations for serious backtesting: IBKR rate-limits at 45 requests/second, Kraken's spot OHLC caps at 720 entries per call, and neither provides the multi-decade, multi-asset depth needed for rigorous strategy validation. The existing CSV loader handles basic imports but isn't designed for the scale and schema diversity of a 387GB institutional dataset spanning stocks, ETFs, futures, FX, crypto, indices, and delisted stocks — each with its own CSV format quirks.

Without deep historical data, backtests are limited in scope. Without delisted stocks, they suffer survivorship bias — the silent credibility killer of most retail backtesting setups. Without visual exploration, data quality issues go unnoticed until a strategy produces suspicious results. And without catalog isolation, imported research-grade data risks being silently augmented by live feeds with different adjustment methodologies.

## The Solution

**A self-contained research environment: Import, Explore, Backtest.**

**Import Pipeline:** A purpose-built ingestion engine that parses FirstRate Data's 6 distinct CSV schemas (handling FX's YYYYMMDD dates, futures' open interest column, indices' missing volume, and more), validates data quality (OHLC sanity, gap detection, outlier flagging), and converts to NTrader's Parquet catalog format with correct Nautilus instrument IDs. The pipeline generates a pre-import manifest (file counts, schemas detected, date ranges, disk estimate) before committing any data, supports checkpointed resumable imports for the full 387GB dataset, and stores data in an isolated catalog enforced via a dedicated `FIRSTRATE_CATALOG_PATH` environment variable. Supplementary data — company profiles, dividend history, stock splits — is linked to each asset as metadata.

**Data Explorer:** A web-based interactive chart page integrated into NTrader's existing FastAPI/HTMX UI, built on TradingView Lightweight Charts. Users browse available tickers with coverage statistics and per-ticker data health indicators, then explore price history across four timeframes (1-min, 15-min, 1-hour, daily). Progressive loading delivers data in time-range slices via a dedicated chart API (`GET /api/chart/{ticker}?start=&end=&tf=`) as the user zooms and pans — making even decades of 1-min data navigable without loading gigabytes into the browser. Supplementary data (company profiles, dividends, splits) is surfaced in the explorer as asset metadata; backtest consumption of supplementary data is planned for a future phase.

**Backtest Integration:** The imported FirstRate catalog plugs into NTrader's existing BacktestOrchestrator as a new data source, enabling users to select tickers and timeframes from the explorer and launch backtests directly against the pristine historical dataset.

## What Makes This Different

- **Zero manual preprocessing** — drop FirstRate Data files and the system handles schema detection, validation, and conversion across all 6 CSV formats. No pandas scripts, no manual column mapping.
- **Catalog integrity by design** — the historical catalog is append-only and source-tagged, enforced via dedicated catalog paths (not just convention). Every backtest against this data is reproducible.
- **Survivorship-bias-free** — 6,887 delisted stocks included as a first-class asset class, enabling rigorous strategy validation that most personal setups silently lack.
- **Closed research loop** — data import, visual exploration, quality validation, and strategy execution in a single application. No context-switching between tools.
- **Scale-appropriate architecture** — resumable checkpointed imports, progressive chart loading, and time-range API slicing handle the 387GB dataset without requiring exotic infrastructure.

## Who This Serves

**Primary user: the NTrader operator** — a quantitative developer running backtests on personal infrastructure. Wants deep historical data across multiple asset classes, visual confirmation that data looks correct before trusting backtest results, and the ability to go from "I wonder how this strategy performs on FX" to running a backtest without writing custom import scripts.

## Success Criteria

- All 7 asset classes imported into isolated Parquet catalog with correct Nautilus instrument IDs
- Pre-import manifest generated and validated before bulk import commits to disk
- Import pipeline is resumable — can recover from interruption without full restart
- Data quality validation passes: OHLC sanity (high >= low, volume >= 0), gap detection per ticker, outlier flagging
- Supplementary metadata (company profiles, dividends, splits) linked and queryable in the explorer
- Data explorer renders charts at 4 timeframes with progressive loading (smooth pan/zoom on 1-min data)
- A backtest using a built-in strategy against an imported asset class completes without error and produces a results record in the DB
- Incremental import detects per-ticker coverage gaps and fills them without re-importing existing data
- Parquet catalog load time remains <500ms for 1 year of 1-min bars (existing performance target)

## Scope

### In Scope (V1)
- FirstRate Data CSV import pipeline supporting all 7 asset classes (phased: validate with one asset class first, then expand)
- Pre-import manifest and data validation pipeline
- Resumable, checkpointed bulk import
- Isolated Parquet catalog enforced via `FIRSTRATE_CATALOG_PATH` environment variable
- Supplementary data ingestion: company profiles, dividend history, stock splits (explorer-only metadata in V1; backtest consumption in future phase)
- Data explorer page with statistics, coverage visualization, data health indicators, and interactive charting
- Three native timeframes imported from provider: 1-min, 1-hour, daily
- 15-min timeframe resampled from 1-min data (resampling strategy: sub-1-hour resamples from 1-min, above-1-hour resamples from 1-hour)
- Progressive chart loading via time-range API slicing
- BacktestOrchestrator integration for the new data source
- Incremental import support (per-ticker gap detection and fill from new data batches)

### Out of Scope (V1)
- Gap-filling via IBKR/Kraken live feeds (future enhancement)
- Supplementary data influencing backtest execution (dividends/splits as backtest events — future phase)
- Additional resampled timeframes (5-min, 30-min) beyond the four supported
- Unadjusted or split-only price variants (only split+dividend adjusted included)
- Real-time data streaming or live trading integration
- Multi-user access or authentication
- Backtest trade signal overlay on explorer charts (future enhancement)
- Cross-asset correlation views

## Roadmap Thinking

**Phase 1 (MVP):** Import pipeline for Stocks (7,794 tickers, `company_profiles.csv` ships with the bundle for full ticker→exchange mapping, validated end-to-end via a 2026-04-11 smoke test against real FirstRate data) + data explorer with progressive charting + backtest integration. Validates the end-to-end workflow as a complete vertical slice — from raw CSV to visual exploration to executed backtest. *Originally scoped as ETFs; pivoted to Stocks during Story 1-5 when we discovered the ETF bundle lacks a profiles file.*

**Phase 2:** Expand import to remaining asset classes. ETFs come next (same 6-column schema — the shared `FirstRateCsvParser` already handles them — gated on sourcing instrument metadata via a dedicated FMP-backed metadata loader story). Then Delisted Stocks for survivorship-bias coverage, plus Futures / FX / Crypto / Indices per their own schemas. Incorporate supplementary metadata. Incremental import support. Delisted stock namespace handling (ticker reuse disambiguation).

**Phase 3:** Cross-asset exploration features (compare tickers across asset classes), advanced filtering (by sector, exchange, date range coverage), backtest trade signal overlay on explorer charts, and performance optimizations for the full 387GB dataset.

**Future:** IBKR/Kraken gap-filling for the FirstRate catalog (on explicit request), scheduled auto-refresh from new FirstRate Data batches, additional data provider integrations, custom universe construction from the catalog.
