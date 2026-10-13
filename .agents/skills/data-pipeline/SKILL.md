---
name: data-pipeline
description: >
  Use when loading market data, working with the Parquet catalog, fetching from IBKR,
  converting CSV data, or diagnosing data availability issues. Covers the DataCatalogService
  API, catalog structure, and IBKR client configuration.
---

# Data Pipeline Guide

> See also: `docs/agent/nautilus.md` (IBKR client, Parquet catalog)

## Data Flow

```
CSV files / IBKR API → nautilus_converter → ParquetDataCatalog → BacktestEngine.add_data(bars)
```

## Bar Type Format

```
{SYMBOL}.{VENUE}-{STEP}-{STEP_TYPE}-{PRICE_TYPE}-EXTERNAL
```

Example: `QQQ.NASDAQ-1-DAY-LAST-EXTERNAL`

| Component | Examples | Notes |
|-----------|----------|-------|
| SYMBOL.VENUE | `AAPL.NASDAQ`, `QQQ.NASDAQ` | Instrument identifier |
| STEP | `1`, `5`, `15` | Bar duration number |
| STEP_TYPE | `MINUTE`, `DAY`, `HOUR` | Bar duration unit |
| PRICE_TYPE | `LAST`, `MID`, `BID`, `ASK` | Price aggregation type |
| Source | `EXTERNAL` (real data), `INTERNAL` (mock) | Data origin |

## DataCatalogService API

```python
from src.services.data_catalog import DataCatalogService
service = DataCatalogService()  # Uses NAUTILUS_PATH env or ./data/catalog

# Load from catalog, fetch from IBKR if missing (primary method)
bars = await service.fetch_or_load(
    instrument_id="AAPL.NASDAQ",
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 12, 31, tzinfo=timezone.utc),
    bar_type_spec="1-MINUTE-LAST",
)

# Query bars directly from catalog (no IBKR fallback)
bars = service.query_bars(instrument_id="AAPL.NASDAQ", ...)

# Other methods
service.write_bars(bars, correlation_id="backtest-123")
avail = service.get_availability("AAPL.NASDAQ", "1-MINUTE-LAST")
gaps = service.detect_gaps("AAPL.NASDAQ", "1-MINUTE-LAST", start_date=..., end_date=...)
catalog_data = service.scan_catalog()
instrument = service.load_instrument("AAPL.NASDAQ")
```

### Critical Gotcha: `catalog.bars()` Parameter

```python
# WRONG — Using BarType objects returns ALL bar types
bars = catalog.bars(bar_types=[BarType.from_str("...")])

# CORRECT — Must be list of STRINGS
bars = catalog.bars(bar_types=["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"])
```

## IBKR Client

All connection settings via environment variables through `IBKRSettings`.
See `docs/agent/nautilus.md` for full IBKR configuration details.

The client is created lazily on first access — no connection until data is actually needed from IBKR.

## Key Source Files

- `src/services/data_catalog.py` — Catalog facade (primary entry point)
- `src/services/ibkr_client.py` — IBKR connection and historical data
- `src/services/csv_loader.py` — CSV file loading
- `src/services/nautilus_converter.py` — Data format conversion
- `src/models/catalog_metadata.py` — CatalogAvailability model
