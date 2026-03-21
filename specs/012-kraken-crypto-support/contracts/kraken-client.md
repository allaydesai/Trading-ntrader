# Contract: KrakenHistoricalClient

**Purpose**: Fetch historical OHLCV bar data from Kraken and return Nautilus-compatible objects.
**Mirrors**: `IBKRHistoricalClient` interface pattern from `src/services/ibkr_client.py`

## Interface

### Constructor

```python
KrakenHistoricalClient(
    api_key: str = "",
    api_secret: str = "",
    rate_limit: int = 10,
    default_maker_fee: Decimal = Decimal("0.0016"),
    default_taker_fee: Decimal = Decimal("0.0026"),
)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `is_connected` | bool | Whether the client has been initialized and validated |

### Methods

#### `async connect(timeout: int = 30) -> dict`

Initialize the Kraken client and validate credentials. Returns connection metadata.

**Returns**: `{"connected": True, "pairs_available": int}`
**Raises**: `KrakenConnectionError` if credentials are invalid or API unreachable.

#### `async disconnect()`

Gracefully close any open connections.

#### `async fetch_bars(instrument_id: str, start: datetime, end: datetime, bar_type_spec: str = "1-MINUTE-LAST") -> tuple[list[Bar], Instrument | None]`

Fetch historical OHLCV bars for a Kraken spot pair.

**Parameters**:
- `instrument_id`: Nautilus format, e.g., `"BTC/USD.KRAKEN"`
- `start`: UTC datetime for range start
- `end`: UTC datetime for range end
- `bar_type_spec`: Bar specification, e.g., `"1-MINUTE-LAST"`, `"1-HOUR-LAST"`, `"1-DAY-LAST"`

**Returns**: `(bars, instrument)` tuple — list of Nautilus `Bar` objects and a `CurrencyPair` instrument definition.

**Raises**:
- `KrakenConnectionError`: API unreachable or auth failure
- `KrakenRateLimitError`: Rate limit exceeded (includes `retry_after`)
- `DataNotFoundError`: Pair not found or no data for range

**Behavior**:
1. Resolve `instrument_id` to Kraken pair format via pair mapping
2. Fetch asset pair metadata (price/size precision) for instrument construction
3. Fetch OHLCV data via Futures Charts API with pagination (`more_candles`)
4. Convert Kraken OHLCV records to Nautilus `Bar` objects
5. Construct `CurrencyPair` instrument from asset pair metadata
6. Return `(bars, instrument)`

#### `async fetch_asset_pairs() -> dict[str, CryptoInstrumentDefinition]`

Fetch available trading pairs from Kraken.

**Returns**: Dict mapping user pair names (e.g., `"BTC/USD"`) to instrument definitions.
**Raises**: `KrakenConnectionError` if API unreachable.

## Bar Type Mapping

| User Spec | Kraken Charts Resolution | Nautilus BarType |
|-----------|-------------------------|-----------------|
| 1-MINUTE-LAST | 1m | BTC/USD.KRAKEN-1-MINUTE-LAST-EXTERNAL |
| 5-MINUTE-LAST | 5m | BTC/USD.KRAKEN-5-MINUTE-LAST-EXTERNAL |
| 15-MINUTE-LAST | 15m | BTC/USD.KRAKEN-15-MINUTE-LAST-EXTERNAL |
| 1-HOUR-LAST | 1h | BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL |
| 4-HOUR-LAST | 4h | BTC/USD.KRAKEN-4-HOUR-LAST-EXTERNAL |
| 1-DAY-LAST | 1d | BTC/USD.KRAKEN-1-DAY-LAST-EXTERNAL |

## Error Types

```python
class KrakenConnectionError(CatalogError):
    """Kraken API connection or authentication failure."""

class KrakenRateLimitError(CatalogError):
    """Kraken API rate limit exceeded."""
    retry_after: int  # seconds
```
