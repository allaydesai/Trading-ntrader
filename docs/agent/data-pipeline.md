# Data Pipeline

## DataCatalogService

`src/services/data_catalog.py` — central data access layer with lazy initialization.

- Lazy-initializes IBKR and Kraken clients only when first needed
- Maintains in-memory `availability_cache` for fast checks (key: `{instrument_id}_{bar_type_spec}`)
- Rebuilds cache on startup; invalidated if catalog changes
- `NAUTILUS_PATH` env var controls catalog location (defaults to `./data/catalog`)

**Data source routing** (in BacktestOrchestrator):
- `catalog` (default) — Parquet files on disk
- `ibkr` — fetch from Interactive Brokers TWS/Gateway
- `kraken` — fetch from Kraken API
- `mock` — test data

## Symbol Resolution

`_resolve_instrument_id()` in `backtest_request.py`:
- Bare symbols (e.g., "GDX") resolved to full IDs (e.g., "GDX.ARCA") via catalog lookup
- Falls back to `{symbol}.NASDAQ` if no catalog entry
- Enables user-friendly CLI without forcing full instrument ID format

## IBKR Client

`src/services/ibkr_client.py` — `IBKRHistoricalClient` class.

**Connection** via `src/config.py:IBKRSettings` (all env vars):
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`
- `TWS_USERNAME`, `TWS_PASSWORD`, `TWS_ACCOUNT`
- `IBKR_TRADING_MODE` (paper/live), `IBKR_READ_ONLY` (default: true)

**Rate limit**: 45 req/s (90% of IBKR's 50/s hard limit) via `RateLimiter` class.
**Instrument resolution**: Qualifies contracts to primary exchanges (e.g., ARCA, NASDAQ) — important for correct data.
**LogGuard integration**: `_guard_nautilus_logging()` context manager prevents double-init when IBKR client starts first.

## Kraken Client

`src/services/kraken_client.py` — `KrakenHistoricalClient` class.

**Configuration** via `src/config.py:KrakenSettings`:
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` (must be paired — both set or both empty)
- `KRAKEN_RATE_LIMIT` (default: 10 req/s)
- `KRAKEN_DEFAULT_MAKER_FEE` / `KRAKEN_DEFAULT_TAKER_FEE`

**Pair mapping** — users specify standard pairs (BTC/USD), internally mapped to:
- REST API: XXBTZUSD
- Charts API: XBT/USD
- Nautilus InstrumentId: BTC/USD.KRAKEN

**Data source**: Futures Charts API (`/api/charts/v1/spot/{symbol}/{resolution}`) — supports arbitrary date ranges with pagination (Spot OHLC limited to 720 entries).

Uses both `SpotMarket` and `FuturesMarket` from kraken SDK. Rate limit: sliding window.

## Parquet Catalog

Market data stored as Parquet files under Nautilus catalog structure.
- `data_catalog.py` + `data_service.py` handle reads/writes
- CSV → `csv_loader.py` → `nautilus_converter.py` → Parquet
- Kraken → `kraken_client.py` → same Parquet structure
- Parquet catalog is the **single source of truth** for market data

## Exceptions

`src/services/exceptions.py`:
- `DataNotFoundError` — includes instrument_id, start/end dates; triggers fallback to alternate source
- `CatalogCorruptionError` — includes file_path and original_error
- `IBKRConnectionError`, `KrakenConnectionError` — connection unavailable
- `RateLimitExceededError`, `KrakenRateLimitError` — includes `retry_after` for backoff

## CLI Data Commands

`src/cli/commands/` — shared helper `_backtest_helpers.py`:
- `load_backtest_data()` orchestrates loading from any source
- Returns `(bars: List[Bar], instrument: Instrument)`
- Handles symbol resolution via DataCatalogService
- Rich progress bars for long operations; quiet mode for web UI
