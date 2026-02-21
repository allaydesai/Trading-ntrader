---
name: data-pipeline
description: >
  Use when loading market data, working with the Parquet catalog, fetching from IBKR,
  converting CSV data, or diagnosing data availability issues. Covers the DataCatalogService
  API, catalog structure, and IBKR client configuration.
---

# Data Pipeline Guide

## Data Flow

```
CSV files / IBKR API
        |
        v
nautilus_converter (converts to Nautilus Bar objects)
        |
        v
ParquetDataCatalog (persists as Parquet files)
        |
        v
BacktestEngine.add_data(bars)
```

## Parquet Catalog Structure

```
{NAUTILUS_PATH}/
└── data/
    └── bar/
        └── {instrument_id}-{bar_type_spec}-EXTERNAL/
            └── {start_timestamp}_{end_timestamp}.parquet
```

Example:
```
data/catalog/data/bar/AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL/
  2024-01-02T09-30-00-000000000Z_2024-01-02T15-59-00-000000000Z.parquet
```

The `NAUTILUS_PATH` env var (or `./data/catalog` default) is the catalog root.

## Bar Type Format

```
{SYMBOL}.{VENUE}-{STEP}-{STEP_TYPE}-{PRICE_TYPE}-EXTERNAL
```

| Component | Examples | Notes |
|-----------|----------|-------|
| SYMBOL.VENUE | `AAPL.NASDAQ`, `QQQ.NASDAQ` | Instrument identifier |
| STEP | `1`, `5`, `15` | Bar duration number |
| STEP_TYPE | `MINUTE`, `DAY`, `HOUR` | Bar duration unit |
| PRICE_TYPE | `LAST`, `MID`, `BID`, `ASK` | Price aggregation type |
| Source | `EXTERNAL` (real data), `INTERNAL` (mock) | Data origin |

Full example: `QQQ.NASDAQ-1-DAY-LAST-EXTERNAL`

## DataCatalogService API

```python
from src.services.data_catalog import DataCatalogService
service = DataCatalogService()  # Uses NAUTILUS_PATH env or ./data/catalog
```

### Core Methods

```python
# Load from catalog, fetch from IBKR if missing (primary method)
bars = await service.fetch_or_load(
    instrument_id="AAPL.NASDAQ",
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 12, 31, tzinfo=timezone.utc),
    bar_type_spec="1-MINUTE-LAST",
)

# Query bars directly from catalog (no IBKR fallback)
bars = service.query_bars(
    instrument_id="AAPL.NASDAQ",
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 12, 31, tzinfo=timezone.utc),
    bar_type_spec="1-MINUTE-LAST",
)

# Write bars to catalog
service.write_bars(bars, correlation_id="backtest-123")

# Check data availability (from in-memory cache)
avail = service.get_availability("AAPL.NASDAQ", "1-MINUTE-LAST")
if avail:
    print(f"Data: {avail.start_date} to {avail.end_date}")

# Detect gaps in data
gaps = service.detect_gaps(
    "AAPL.NASDAQ", "1-MINUTE-LAST",
    start_date=datetime(2024, 1, 1),
    end_date=datetime(2024, 12, 31),
)

# Scan all available data
catalog_data = service.scan_catalog()  # Dict[instrument_id, List[CatalogAvailability]]

# Load instrument definition from catalog
instrument = service.load_instrument("AAPL.NASDAQ")
```

### Critical Gotcha: `catalog.bars()` Parameter

```python
# WRONG — Using BarType objects causes Nautilus to return ALL bar types
bars = catalog.bars(bar_types=[BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")])

# CORRECT — Must be list of STRINGS
bars = catalog.bars(bar_types=["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"])
```

The parameter is `bar_types` (plural) and expects `list[str]`, NOT `list[BarType]`.

## IBKR Client Configuration

All connection settings via environment variables through `IBKRSettings`:

| Variable | Default | Typical |
|----------|---------|---------|
| `IBKR_HOST` | `127.0.0.1` | `127.0.0.1` |
| `IBKR_PORT` | `7497` | `4002` (Gateway paper) |
| `IBKR_CLIENT_ID` | `10` | `10` |

### Lazy Initialization

The IBKR client is created lazily on first access — this avoids unnecessary connection attempts during backtests when data is already in the catalog:

```python
# Client created only when .ibkr_client property is first accessed
service = DataCatalogService()  # No IBKR connection yet
bars = service.query_bars(...)   # Still no connection (catalog only)
bars = await service.fetch_or_load(...)  # NOW creates IBKR client if data missing
```

### Rate Limiting

IBKR enforces ~45 requests/second. The client uses exponential backoff retry:
- Max retries: 3 (configurable)
- Backoff: 2^retry_count seconds (2s, 4s, 8s)

## Data Conversion

### CSV to Nautilus
```python
from src.services.csv_loader import CSVLoader
from src.services.nautilus_converter import NautilusConverter

# Load and convert
loader = CSVLoader()
converter = NautilusConverter()
```

### Database to Nautilus
```python
from src.services.data_service import DataService
service = DataService()
bars = service.convert_to_nautilus_bars(market_data, instrument_id, instrument)
```

## Key Source Files

- `src/services/data_catalog.py` — Catalog facade (primary entry point)
- `src/services/ibkr_client.py` — IBKR connection and historical data
- `src/services/csv_loader.py` — CSV file loading
- `src/services/nautilus_converter.py` — Data format conversion
- `src/models/catalog_metadata.py` — CatalogAvailability model
