# Research: Kraken Crypto Data Support

**Branch**: `012-kraken-crypto-support` | **Date**: 2026-02-28

## R-001: Kraken OHLC API Historical Data Retrieval

**Decision**: Use the Kraken Futures Charts API (`/api/charts/v1/spot/:symbol/:resolution`) as the primary historical data source, not the Spot REST OHLC endpoint.

**Rationale**: The Spot REST OHLC endpoint (`GET /0/public/OHLC`) has a hard limit of 720 entries maximum — older data cannot be retrieved regardless of the `since` parameter. For 1-minute bars, that's only 12 hours of history. The Futures Charts API supports arbitrary date ranges via `from`/`to` timestamps with pagination (`more_candles: true`), making it suitable for backtesting over months or years. It also supports a `spot` tick type for spot market data.

**Alternatives considered**:
- Spot REST OHLC only: Rejected due to 720-entry hard limit (insufficient for backtesting)
- Third-party data providers (e.g., Tardis): Adds dependency, out of scope for initial implementation
- Kraken WebSocket for historical replay: WebSocket is for real-time only, not historical data

**Spot OHLC details** (for reference):
- Endpoint: `GET https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60`
- Returns: `[time, open, high, low, close, vwap, volume, count]` — all strings except time/count
- Max 720 entries, most recent only
- Intervals: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600 minutes
- `since` parameter: For incremental polling only, cannot retrieve older data

**Futures Charts API details** (primary choice):
- Endpoint: `GET https://futures.kraken.com/api/charts/v1/spot/{symbol}/{resolution}?from=X&to=Y`
- Returns: `{candles: [{time, open, high, low, close, volume}], more_candles: bool}`
- Resolutions: 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w
- Supports pagination via `more_candles` flag
- Public endpoint (no auth required for spot data)

## R-002: Kraken API Rate Limits

**Decision**: Implement conservative rate limiting at 10 requests/second for Spot REST and rely on the generous public limits for Futures Charts API.

**Rationale**: Kraken Spot REST rate limiting uses a call counter system (not simple req/sec). Public endpoints cost 1 per call. The counter max is 15 (Starter) to 20 (Pro), decaying at 0.33–1.0/sec depending on tier. Conservative approach avoids `EAPI:Rate limit exceeded` errors. The Futures Charts API public endpoints have no cost against rate-limit budgets, so they're essentially unlimited for reasonable use.

**Rate limit tiers (Spot REST)**:

| Tier | Max Counter | Decay Rate |
|------|-------------|------------|
| Starter | 15 | -0.33/sec |
| Intermediate | 20 | -0.5/sec |
| Pro | 20 | -1/sec |

**Alternatives considered**:
- No rate limiting (rely on API errors): Rejected — risks account penalties and `EAPI:Rate limit exceeded` errors
- Exact counter tracking: More complex, diminishing returns for batch historical fetching
- Per-tier configuration: Over-engineering for initial implementation; conservative default covers all tiers

## R-003: Kraken Pair Naming Convention

**Decision**: Use Nautilus convention `BTC/USD.KRAKEN` for instrument IDs. Map to/from Kraken's native formats (`XXBTZUSD`, `XBT/USD`) within the client.

**Rationale**: Kraken uses multiple naming formats:
- REST API pairs: `XXBTZUSD` (X-prefix for crypto, Z-prefix for fiat)
- Display format: `XBT/USD` (Kraken uses XBT instead of BTC for Bitcoin)
- Futures Charts: `XBT/USD` in URL path

Nautilus convention is `SYMBOL.VENUE`. Using standard crypto symbols (BTC not XBT) is more intuitive. The mapping layer in the client handles conversion.

**Mapping examples**:

| User Input | Nautilus ID | Kraken REST | Kraken Charts URL |
|-----------|-------------|-------------|-------------------|
| BTC/USD | BTC/USD.KRAKEN | XXBTZUSD | XBT/USD |
| ETH/USD | ETH/USD.KRAKEN | XETHZUSD | ETH/USD |
| SOL/USD | SOL/USD.KRAKEN | SOLUSD | SOL/USD |

**Alternatives considered**:
- Use Kraken's native naming (XBT): Confusing for users, inconsistent with industry standard
- Use Kraken's API format (XXBTZUSD): Opaque, not user-friendly
- Let users specify Kraken's format directly: Added complexity, error-prone

## R-004: Nautilus Trader Crypto Instrument Creation

**Decision**: Create `CurrencyPair` instruments programmatically using Kraken's asset pair metadata (`/0/public/AssetPairs` endpoint) for precision values, fees, and constraints.

**Rationale**: Unlike IBKR (which returns fully-formed Nautilus Instrument objects), Kraken data requires manual instrument construction. The `CurrencyPair` class is the correct Nautilus type for spot crypto pairs. Kraken's AssetPairs endpoint provides all needed metadata (price/size precision, min order size, fees).

**CurrencyPair construction pattern**:
```python
CurrencyPair(
    instrument_id=InstrumentId.from_str("BTC/USD.KRAKEN"),
    raw_symbol=Symbol("BTC/USD"),
    base_currency=Currency.from_str("BTC"),
    quote_currency=Currency.from_str("USD"),
    price_precision=1,           # From AssetPairs.pair_decimals
    size_precision=8,            # From AssetPairs.lot_decimals
    price_increment=Price.from_str("0.1"),
    size_increment=Quantity.from_str("0.00000001"),
    maker_fee=Decimal("0.0016"), # Kraken default maker fee
    taker_fee=Decimal("0.0026"), # Kraken default taker fee
    ...
)
```

**Alternatives considered**:
- Hardcode instrument definitions: Brittle, doesn't scale to all Kraken pairs
- Use Nautilus TestInstrumentProvider: Only for testing, not production
- Store instrument definitions in config: Over-engineering, Kraken API has the data

## R-005: Bar Conversion from Kraken to Nautilus Format

**Decision**: Convert Kraken OHLCV arrays to Nautilus `Bar` objects using `BarType.from_str()` and nanosecond Unix timestamps.

**Rationale**: The existing codebase (csv_loader.py, nautilus_converter.py) already has patterns for creating Bar objects from raw OHLCV data. The conversion is straightforward: parse Kraken's string prices, construct Bar objects, and write to the Parquet catalog.

**BarType format**: `BTC/USD.KRAKEN-1-MINUTE-LAST-EXTERNAL`

**Kraken OHLCV → Nautilus Bar mapping**:

| Kraken Field | Nautilus Bar Field | Transform |
|-------------|-------------------|-----------|
| time (int, seconds) | ts_event (int, nanoseconds) | × 1_000_000_000 |
| open (string) | open (Price) | Price.from_str() |
| high (string) | high (Price) | Price.from_str() |
| low (string) | low (Price) | Price.from_str() |
| close (string) | close (Price) | Price.from_str() |
| volume (string/int) | volume (Quantity) | Quantity.from_str() |

## R-006: DataCatalogService Integration Strategy

**Decision**: Extend `DataCatalogService` with a `data_source` parameter in `fetch_or_load()` and support lazy-initialized Kraken client alongside the existing IBKR client.

**Rationale**: The existing `DataCatalogService` is well-designed with lazy client initialization, caching, and gap detection. The simplest extension is adding a `kraken_client` parameter to `__init__` (same pattern as `ibkr_client`) and a `data_source` parameter to `fetch_or_load()` to select which client to use. This preserves backward compatibility and reuses all existing caching/Parquet logic.

**Alternatives considered**:
- Create separate `KrakenDataCatalogService`: Code duplication (caching, gap detection, Parquet logic)
- Create abstract `DataSourceClient` Protocol: Over-engineering for 2 clients; can refactor later if more sources added
- Pass client directly to `fetch_or_load()`: Leaks client management to callers

## R-007: python-kraken-sdk Library Assessment

**Decision**: Use `python-kraken-sdk` as the designated Kraken API client library, specifically `SpotAsyncClient` for async operations and `FuturesClient` for historical charts data.

**Rationale**: The library is actively maintained, provides typed wrappers for all Kraken REST endpoints, supports both sync and async patterns, and handles authentication automatically. It's the user's explicitly chosen library.

**Key classes to use**:
- `kraken.spot.SpotAsyncClient`: For asset pair metadata, recent OHLC data
- `kraken.futures.FuturesClient`: For Futures Charts API (historical spot data)
- Built-in error handling for Kraken API errors

**Alternatives considered**:
- Raw HTTP requests with httpx/aiohttp: More code, less type safety, reinvents existing wrappers
- ccxt library: More generic but less Kraken-specific, heavier dependency
- krakenex: Older, less maintained, sync-only
