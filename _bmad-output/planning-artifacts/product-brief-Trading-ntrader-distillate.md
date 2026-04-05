---
title: "Product Brief Distillate: Trading-ntrader"
type: llm-distillate
source: "product-brief-Trading-ntrader.md"
created: "2026-04-04"
purpose: "Token-efficient context for downstream PRD creation"
---

# Product Brief Distillate: NTrader Historical Data Import & Explorer

## Dataset Specifications

- **Provider:** FirstRate Data — institutional-grade, used by NBER, Boston Fed, Stanford; ~$300-400 per bundle
- **Total size:** ~387GB raw CSV, 20,934 tickers across 7 asset classes
- **Asset class breakdown:** US Stocks (7,794 active), Delisted Stocks (6,887), ETFs (5,072), Futures (132), US Indices (128), FX (79), Crypto (74)
- **Native timeframes being imported:** 1-min, 1-hour, daily. Provider also offers 5-min and 30-min (not downloaded; could add later)
- **Resampling strategy decided:** sub-1-hour resampled from 1-min (e.g., 15-min), above-1-hour resampled from 1-hour. Four timeframes in explorer: 1-min, 15-min, 1-hour, daily
- **Price adjustment:** Stocks/ETFs/Delisted are split+dividend adjusted; Futures are continuous ratio-adjusted; Crypto/FX/Indices have no adjustment. Only adjusted variant downloaded — unadjusted/split-only out of scope
- **Supplementary data:** company_profiles.csv (7,664 entries: ticker, name, country, state, exchange, sector, industry, IPO date), dividend history (3,816 tickers), stock split history (2,688 tickers) — all stocks only

## CSV Schema Details (6 Distinct Formats)

- **Stocks/ETFs/Delisted (daily):** 6 cols, no header: `Date,Open,High,Low,Close,Volume` — Date format `YYYY-MM-DD`, volume integer
- **Stocks/ETFs/Delisted (1-min):** 6 cols, no header: `Datetime,Open,High,Low,Close,Volume` — Datetime `YYYY-MM-DD HH:MM:SS`, volume float
- **Crypto (daily/1-min):** Same 6-col format as stocks, volume is decimal (native asset units)
- **FX (daily):** 6 cols, no header: `Date,Open,High,Low,Close,Volume` — **Date format is YYYYMMDD** (differs from all others), volume is tick count
- **FX (1-min):** **7 columns** — date and time split: `Date,Time,Open,High,Low,Close,Volume` — Date `YYYYMMDD`, Time `HH:MM:SS`. This is the only schema with a separate time column
- **Futures (daily):** **7 columns:** `Date,Open,High,Low,Close,Volume,OpenInterest` — open interest only on daily bars
- **Futures (1-min):** 6 cols: `Datetime,Open,High,Low,Close,Volume` — same as stocks
- **Index (daily/1-min):** **5 columns** — no volume: `Date(time),Open,High,Low,Close`
- **All files are headerless CSV**
- **Supplementary dividends:** 2 cols reverse chronological: `Date,Dividend`
- **Supplementary splits:** 2 cols reverse chronological: `Date,SplitRatio` (e.g., 2 = 2-for-1 split)

## Directory Structure & Sharding

- Stocks/ETFs: sharded into 26 subdirs by starting letter (`stock_A_full_1min_adjsplitdiv_{hash}/`)
- Delisted Stocks: 5 archive shards (`archive_1_...` through `archive_5_...`)
- **Some Delisted_Stocks_1min shards are .zip archives** requiring extraction before parsing
- All other asset types: flat directories, one file per ticker
- Filename patterns vary: stocks use `{TICKER}_full_1day_adjsplitdiv.txt`, futures use `{SYMBOL}_full_1day_continuous_ratio_adjusted.txt`, crypto/FX/index use `{TICKER}_full_1day.txt`
- `file_list_structured.csv` provides a master index: `ticker,entity_name,start_date,asset_type` (20,934 entries)

## Technical Context (Existing NTrader Architecture)

- **Parquet catalog is single source of truth** for market data: `./data/catalog/{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet`
- **DataCatalogService** handles lazy client init, availability caching, fallback routing (catalog → IBKR → Kraken → mock)
- **BacktestOrchestrator** has `data_source` enum for routing — adding FirstRate as a new source follows established pattern
- **Existing CSV path:** `csv_loader.py` → `nautilus_converter.py` → Parquet. No DB intermediary
- **Symbol resolution:** `_resolve_instrument_id()` does bare symbol → catalog lookup → `{symbol}.NASDAQ` fallback
- **Web UI:** FastAPI + Jinja2 + HTMX + TradingView Lightweight Charts v5.0, module loading chain in strict order
- **Chart API endpoints** currently return full series — need time-range slicing for progressive loading
- **Dual DB pattern:** web uses AsyncSession/asyncpg, CLI uses sync Session/psycopg2
- **Backtest engine is single-use** — create new instance each time, extract results before disposal
- **LogGuard constraint:** never instantiate Logger directly, use `set_nautilus_log_guard()`
- **Catalog path controlled by `NAUTILUS_PATH` env var** — FirstRate catalog needs its own path variable (`FIRSTRATE_CATALOG_PATH`)

## Nautilus Instrument ID Mapping (Critical Complexity)

- FirstRate uses bare tickers (AAPL, EURUSD, ES) but Nautilus requires venue-qualified IDs (AAPL.NASDAQ, EUR/USD.FXCM, ES.CME)
- **Stocks/ETFs:** company_profiles.csv has exchange column — can derive `{TICKER}.{EXCHANGE}`
- **Delisted stocks:** original exchange may be lost; ticker reuse means same ticker could map to different companies over time — **namespace disambiguation deferred to PRD**
- **Futures:** need exchange mapping (ES → ES.CME) and contract handling for continuous contracts
- **FX:** pair naming varies across systems (EURUSD vs EUR/USD vs FXCM-specific)
- **Crypto:** need exchange suffix (BTC/USD → BTC/USD.FIRSTRATE or similar)
- **Indices:** typically no exchange qualifier needed, but Nautilus may require one

## Decisions Made

- **Phase 1 asset class: ETFs** — simplest schema, 5,072 tickers, well-understood instrument IDs, no FX quirks or futures expiry complexity
- **Supplementary data is explorer-only in V1** — company profiles, dividends, splits displayed in the data explorer UI but do not influence backtest execution. Backtest consumption planned for future phase
- **Catalog isolation enforced via env var** — `FIRSTRATE_CATALOG_PATH` separate from `NAUTILUS_PATH`, not just a convention
- **Import is resumable with checkpointing** — 387GB requires recovery from interruption without full restart
- **Pre-import manifest** generated before committing data (file counts, schemas, date ranges, disk estimate)
- **Data validation at import time** — OHLC sanity (high >= low, volume >= 0), gap detection, outlier flagging
- **Personal use only** — not targeted at broader NautilusTrader community; no multi-user features

## Rejected Ideas / Explicit Out-of-Scope

- **IBKR/Kraken gap-filling for FirstRate catalog** — deferred to future. User wants pristine isolated data
- **Pre-computing all resampled timeframes at import** — rejected in favor of native provider timeframes (1-min, 1-hour, daily) + on-the-fly resampling for 15-min. User will download 1-hour natively from provider rather than resampling from 1-min
- **Unadjusted or split-only price variants** — only split+dividend adjusted downloaded, others out of scope
- **Real-time streaming or live trading** — entirely out of scope
- **Multi-user access / authentication** — personal tool
- **Backtest trade signal overlay on explorer charts** — acknowledged as valuable (opportunity review), deferred to Phase 3
- **Cross-asset correlation views** — deferred to Phase 3
- **Custom universe construction** (e.g., "S&P 500 as of 2010") — acknowledged as valuable (opportunity review), deferred to Future

## Competitive Intelligence

- **Databento and Tardis** already have native NautilusTrader integrations — FirstRate Data does not, this pipeline fills that gap
- **QuantConnect:** cloud-locked, Python.NET translation layer adds latency, no local Parquet catalog
- **Zipline:** effectively abandoned (Python 3.5 era), no native Parquet, hours-long backtests at scale
- **Backtrader:** performance degrades at scale, no catalog management, stagnating community
- **VectorBT:** fast but vectorized-only (no event-driven simulation), no data catalog or explorer
- **QuantRocket:** commercial, opinionated, vendor lock-in, no TradingView-style explorer
- **TradingView Lightweight Charts v5.1:** free 45KB library with data conflation for 10K+ bars at 60fps — already integrated in NTrader as v5.0
- **NautilusTrader community pain point:** no turnkey bulk import tooling; users write custom scripts. Catalog v2 still evolving (GitHub Issue #991)

## Risks & Open Questions for PRD

- **Nautilus Catalog v2 schema may change** — tight coupling to internal Parquet format carries upgrade risk
- **Delisted stock ticker reuse disambiguation** — needs resolution in PRD; affects instrument ID generation and explorer search
- **FX parser is highest-risk** — YYYYMMDD dates + 7-column 1-min schema is unique; bugs silently corrupt timestamps across 79 pairs
- **Resampling performance at scale** — 1 year of 1-min bars is ~3.6M rows; 15-min resampling latency target needed (suggested <200ms server-side)
- **Progressive loading architecture** — TradingView uses binary format + CDN tile server; streaming Parquet slices over FastAPI is fundamentally different. Need to define chunk size, caching strategy, payload format
- **Import concurrency** — even a solo operator may trigger import while using explorer or running backtest. Need to define whether import blocks web server or requires locking
- **Disk budget** — 387GB CSV → Parquet conversion ratio depends on compression (zstd vs snappy); pre-flight disk check needed. Partition strategy (by asset class / ticker / year) affects both storage and query performance
- **Supplementary data storage mechanism undefined** — sidecar Parquet, separate DB table, or metadata attributes? Affects how explorer queries it and how future backtest consumption would work
- **Test strategy for 387GB pipeline** — full dataset can't be in CI. Need representative fixture subsets for each of the 6 CSV schemas. Suggested: backtest correctness validation by comparing results from CSV loader vs new Parquet catalog on a known reference dataset (e.g., 1-year SPY)

## Scope Signals from User

- "At the end of the day I want them all" — all 7 asset classes are the goal, phasing is for validation
- "Keep it simple" — re: data explorer. Statistics, chart, timeframe selection. TradingView-like progressive loading
- "We should leverage since it is available" — re: supplementary data. Not optional
- "For now I am willing to skip this" — re: IBKR/Kraken gap-filling. Clear deferral, mentioned for future context
- "The purpose is primarily for my personal backtesting application" — not community-facing
