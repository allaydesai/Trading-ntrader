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
- `_strategy_params.py` sets strategy-config params by what the config class declares:
  CLI-mode `portfolio_value` (account = `trade_size` x 10) and `--contracts`

## Futures instruments (futures M1)

- Contract specs: `src/services/futures/contract_specs.yaml` (hand-curated, `schema_version` +
  `specs_as_of`, dated approximate margins), validated by `contract_specs.py`
  (`load_contract_specs()`, `get_contract_spec(root)`; errors are `FuturesSpecError` naming the
  field path). Roots: ES, NQ (CME), CL (NYMEX), GC (COMEX), ZN (CBOT).
- `build_futures_contract(spec)` (`src/services/futures/instrument_builder.py`) builds the
  continuous-series `FuturesContract`: id `{ROOT}.{VENUE}` (`ES.CME`), never-expiring window
  (activation 1970-01-01, expiration 2100-01-01 UTC), increment `10^-precision` because
  ratio-adjusted prices are off the tick grid (exchange tick kept in `info["tick_size"]`).
- `build_instrument(asset_class, nautilus_id, ticker, bars)` in
  `src/services/firstrate/backtest_loader.py` is the seam the named-catalog loader uses:
  `FUTURES` rows get a futures contract, every other asset class the unchanged `build_equity`.
- Design notes: `openspec/changes/add-futures-instrument-model/design.md`.

## Futures import (futures M2)

- `import --format firstrate --catalog firstrate-futures --asset-class futures <dir>` ->
  `src/cli/commands/import_futures.py`
  -> `FuturesImportService` (`src/services/firstrate/futures_import_service.py`, a subclass of
  `ImportService`) -> `FirstRateFuturesParser`
  (`src/services/firstrate/parsers/firstrate_futures_parser.py`, registered for `FUTURES`).
- Streams 250k-row pandas chunks, one Parquet part per chunk; verifies from Parquet footer row
  counts plus sampled bars read back by timestamp (never a full read-back).
- Prices: one fixed precision per root (`continuous_price_precision` in the spec). Timestamps:
  same ET->UTC conversion as `FirstRateCsvParser._parse_timestamp`, bars stamped at the open.
- `catalog_instruments` rows come from the spec (no company profiles). A re-run skips a series
  only if its recorded end matches the source AND the Parquet footers hold the recorded count.
- Catalog: `firstrate-futures` (created on first use).
- The equity parser also pads every price of a file to one precision (the widest in the file):
  Nautilus' catalog stores a batch at its first bar's precision and would truncate the rest.
- Design notes: `openspec/changes/add-futures-importer/design.md`.
