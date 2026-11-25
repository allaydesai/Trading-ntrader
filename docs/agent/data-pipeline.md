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

`src/services/kraken_client.py` — `KrakenHistoricalClient` class; the spot logic lives in
`src/services/crypto/kraken_spot.py` (crypto M7).

**Configuration** via `src/config.py:KrakenSettings`:
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`: optional (public endpoints); both set or both empty
- `KRAKEN_RATE_LIMIT` (default: 10 req/s)
- `KRAKEN_DEFAULT_MAKER_FEE` / `KRAKEN_DEFAULT_TAKER_FEE`: default to the Kraken Pro spot
  schedule's default tier in `coin_specs.yaml` (0.0030 / 0.0060); runs charge the schedule

**Pair mapping** — users specify standard pairs (BTC/USD), internally mapped to:
- REST API: the coin spec's AssetPairs key (`XXBTZUSD`, `SOLUSD`), else the altname form
  (`XDGUSD`)
- Nautilus InstrumentId: BTC/USD.KRAKEN

**Data source**: spot REST `OHLC` (`/0/public/OHLC`), the latest 720 candles of an interval.
An older range raises `KrakenSpotRangeError` (a `DataNotFoundError`; the CLI exits 2) before any
request. The Futures Charts API is not used: it lists only futures symbols, and its `spot` tick
type is an index. Nothing is persisted; `_load_kraken_data` calls `fetch_kraken_spot` directly.

**History**: `src/services/crypto/kraken_ohlcvt_download.py` extracts pairs from Kraken's
OHLCVT archive by HTTP range reads; `import --format kraken` (`src/services/crypto/kraken_import.py`)
imports them into a crypto catalog (`kraken-crypto`). See `docs/agent/crypto-backtesting.md`.

## Parquet Catalog

Market data stored as Parquet files under Nautilus catalog structure.
- `data_catalog.py` + `data_service.py` handle reads/writes
- CSV → `csv_loader.py` → `nautilus_converter.py` → Parquet
- Kraken OHLCVT history → `import --format kraken` → same Parquet structure (`kraken-crypto`)
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

## FX import (FX M2)

- `import --format firstrate --catalog firstrate-fx --asset-class fx <dir>` ->
  `src/cli/commands/import_fx.py` (maps each timeframe to `MID`)
  -> `FxImportService` (`src/services/firstrate/fx_import_service.py`, a subclass of
  `FuturesImportService`) -> `FirstRateFxParser`
  (`src/services/firstrate/parsers/firstrate_fx_parser.py`, a futures-parser subclass
  registered for `FX`).
- Rows: intraday `YYYYMMDD,HH:MM:SS,O,H,L,C,V`, daily `YYYYMMDD,O,H,L,C,V`. Each chunk is
  rewritten to the futures `ts,O,H,L,C,V` shape, so ET->UTC, validation and ordering are the
  futures code. A daily row dated `D` is stamped `D-1 17:00` America/New_York (session open).
- Prices: the pair spec's `price_precision`, half-even on the decimal value (float-scaled
  ties such as `128.0105` are re-quantized, `quantize_ties()`); rows changed by more than 1e-9 are
  counted (`N row(s) rounded to 3 decimals`). Bars outside Sun 17:00-Fri 17:00 ET are kept
  and counted (`session anomaly` warnings).
- Bar dirs drop the `/` (`EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL`); `bar_directory()` applies
  `urisafe()`, a mirror of Nautilus' `urisafe_identifier` (absent before 1.219). `source_probe`
  and the dry-run read compact FX dates; the probe returns the parser's stamp so re-runs skip.
- Catalog: `firstrate-fx` (`/srv/market-data/catalogs/firstrate-fx`, imported 2026-11-22: 3
  pairs x 5 timeframes, 767 MB).

## Crypto import (crypto M2)

- `import --format firstrate --catalog firstrate-crypto --asset-class crypto <dir>` ->
  `src/cli/commands/import_crypto.py` (maps each timeframe to `LAST`)
  -> `CryptoImportService` (`src/services/firstrate/crypto_import_service.py`, a subclass of
  `FuturesImportService`) -> `FirstRateCryptoParser`
  (`src/services/firstrate/parsers/firstrate_crypto_parser.py`, a futures-parser subclass
  registered for `CRYPTO`).
- Rows: the equity layout (`YYYY-MM-DD HH:MM:SS,O,H,L,C,V`, daily `YYYY-MM-DD,O,H,L,C,V`), but
  **UTC**: stamps are parsed with the futures `naive_stamps()` and localised to UTC, never to
  New York (no DST rules). A daily row dated `D` opens at `D 00:00 UTC`. Files are
  `{COIN}_full_{tf}.txt`, flat or one directory down.
- Prices: the coin spec's `price_precision` (4), half-even via the FX `quantize_ties()`;
  rows changed by more than 1e-9 are counted (1-minute only in the real data). Volume is coin
  units at the spec's `size_precision` (8): the M1 spike showed a bar volume precision other
  than the instrument's size precision stops the run, so whole-coin volumes are stored at 8 too.
- Warnings (`CryptoAnomalies`, nothing filled or dropped): zero-volume bars, gaps of more than
  10 missing bars (count and longest), and `volume is whole-coin from <ts> UTC`, the first bar of
  the trailing run of whole-number volumes when fractional volumes preceded it.
- Re-runs: the inherited classifier probes sources as New York time and would never skip a UTC
  series, so `CryptoImportService._classify_ticker` calls
  `compute_source_last_date(path, source_tz=timezone.utc)` and also requires the Parquet
  footers to hold the recorded count (and no `.import-incomplete` marker).
- Catalog: `firstrate-crypto` (`/srv/market-data/catalogs/firstrate-crypto`, imported
  2026-11-24: 3 coins x 5 timeframes, 17,419,616 bars, 924 MB, 3 min 12 s, peak RSS 787 MB;
  a re-run skips all 15 series in 22 s). Every series' bar count equals its source line count.
