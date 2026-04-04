# Contract: DataCatalogService Extension

**Purpose**: Extend existing DataCatalogService to support Kraken as a data source alongside IBKR.
**Modifies**: `src/services/data_catalog.py`

## Interface Changes

### Constructor (extended)

```python
DataCatalogService(
    catalog_path: str | Path | None = None,
    ibkr_client: IBKRHistoricalClient | None = None,
    kraken_client: KrakenHistoricalClient | None = None,  # NEW
)
```

### Modified Methods

#### `async fetch_or_load(... data_source: str = "ibkr") -> list[Bar]`

Add `data_source` parameter to select which client fetches missing data.

**Parameters** (new):
- `data_source`: `"ibkr"` (default, backward-compatible) or `"kraken"`

**Behavior change**:
- When `data_source="ibkr"`: Uses `self._ibkr_client` (existing behavior)
- When `data_source="kraken"`: Uses `self._kraken_client` (new path)
- Caching, gap detection, and Parquet storage remain identical regardless of source

### New Property

#### `kraken_client -> KrakenHistoricalClient`

Lazy initialization of Kraken client from settings (same pattern as `ibkr_client` property).

## Backward Compatibility

- Default `data_source="ibkr"` preserves all existing behavior
- No changes to `query_bars()`, `write_bars()`, `detect_gaps()`, or `get_availability()`
- Existing tests continue to pass without modification
