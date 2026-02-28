# Data Model: Kraken Crypto Data Support

**Branch**: `012-kraken-crypto-support` | **Date**: 2026-02-28

## Entities

### KrakenSettings

Configuration for Kraken API connection. Nested within the main `Settings` class, parallel to `IBKRSettings`.

**Fields**:

| Field | Type | Default | Source | Description |
|-------|------|---------|--------|-------------|
| kraken_api_key | str | "" | env var | Kraken API key for authenticated endpoints |
| kraken_api_secret | str | "" | env var | Kraken API secret (base64-encoded) |
| kraken_rate_limit | int | 10 | env var | Max requests per second (conservative) |
| kraken_default_maker_fee | Decimal | 0.0016 | env var | Default maker fee (0.16%) |
| kraken_default_taker_fee | Decimal | 0.0026 | env var | Default taker fee (0.26%) |

**Validation rules**:
- `kraken_api_key` and `kraken_api_secret` must both be set or both be empty
- `kraken_rate_limit` must be between 1 and 20
- Fee values must be between 0 and 1

---

### KrakenPairMapping

Maps between user-facing pair names, Nautilus instrument IDs, and Kraken-native pair identifiers.

**Fields**:

| Field | Type | Description |
|-------|------|-------------|
| user_pair | str | User-friendly name (e.g., "BTC/USD") |
| nautilus_id | str | Nautilus InstrumentId (e.g., "BTC/USD.KRAKEN") |
| kraken_rest_pair | str | Kraken REST API pair (e.g., "XXBTZUSD") |
| kraken_charts_symbol | str | Kraken Futures Charts symbol (e.g., "XBT/USD") |
| base_currency | str | Base asset symbol (e.g., "BTC") |
| quote_currency | str | Quote asset symbol (e.g., "USD") |

**Derived from**: Kraken `/0/public/AssetPairs` endpoint at runtime. Cached locally.

**Special mappings** (Kraken uses non-standard symbols):

| Standard | Kraken |
|----------|--------|
| BTC | XBT |
| DOGE | XDG |

---

### CryptoInstrumentDefinition

Metadata needed to construct a Nautilus `CurrencyPair` instrument for a Kraken spot pair.

**Fields**:

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| instrument_id | str | Derived | Nautilus InstrumentId string |
| raw_symbol | str | AssetPairs | Raw trading pair symbol |
| base_currency | str | AssetPairs.base | Base asset (e.g., "BTC") |
| quote_currency | str | AssetPairs.quote | Quote asset (e.g., "USD") |
| price_precision | int | AssetPairs.pair_decimals | Decimal places for price |
| size_precision | int | AssetPairs.lot_decimals | Decimal places for quantity |
| min_order_size | Decimal | AssetPairs.ordermin | Minimum order quantity |
| maker_fee | Decimal | Settings or AssetPairs | Maker fee rate |
| taker_fee | Decimal | Settings or AssetPairs | Taker fee rate |

**State transitions**: None (immutable after creation)

---

### KrakenOHLCVRecord

Raw OHLCV data from Kraken API before conversion to Nautilus Bar.

**From Futures Charts API**:

| Field | Type | Description |
|-------|------|-------------|
| time | int | Epoch timestamp in milliseconds |
| open | str | Opening price |
| high | str | Highest price |
| low | str | Lowest price |
| close | str | Closing price |
| volume | int | Trading volume |

**From Spot REST API** (array format):

| Index | Type | Description |
|-------|------|-------------|
| 0 | int | Unix timestamp (seconds) |
| 1 | str | Open price |
| 2 | str | High price |
| 3 | str | Low price |
| 4 | str | Close price |
| 5 | str | VWAP |
| 6 | str | Volume |
| 7 | int | Trade count |

**Transforms to**: Nautilus `Bar` object via `Bar(bar_type, open, high, low, close, volume, ts_event, ts_init)`

---

## Entity Relationships

```
Settings
└── KrakenSettings (1:1, nested)

KrakenHistoricalClient
├── uses KrakenSettings (config)
├── uses KrakenPairMapping (pair resolution)
├── fetches KrakenOHLCVRecord[] (raw data)
├── creates CryptoInstrumentDefinition (instrument metadata)
└── returns (List[Bar], CurrencyPair) (Nautilus objects)

DataCatalogService
├── has IBKRHistoricalClient (existing)
├── has KrakenHistoricalClient (new, optional)
└── writes/reads Bar[] to/from ParquetDataCatalog

BacktestRequest
└── data_source: "catalog" | "ibkr" | "kraken" | "mock"
```

## Storage

All Kraken bar data is stored in the existing Parquet catalog using the same format as IBKR data:

```
{catalog_path}/data/bar/BTC-USD.KRAKEN-1-MINUTE-LAST-EXTERNAL/{timestamp}_{timestamp}.parquet
```

No new database tables or schemas required. The existing `backtest_runs` table already stores `data_source` as a string field.
