# Quickstart: Kraken Crypto Data Support

**Branch**: `012-kraken-crypto-support`

## Prerequisites

1. Python 3.11+ with UV package manager
2. Existing NTrader setup (`uv sync`)
3. Kraken account with API access enabled

## Setup

### 1. Install dependency

```bash
uv add python-kraken-sdk
```

### 2. Configure environment variables

Add to `.env`:

```bash
KRAKEN_API_KEY=your-api-key-here
KRAKEN_API_SECRET=your-base64-encoded-secret-here
```

### 3. Verify configuration

```bash
uv run python -c "from src.config import get_settings; s = get_settings(); print(f'Kraken configured: {bool(s.kraken.kraken_api_key)}')"
```

## Usage

### Fetch historical data via CLI

```bash
# Fetch BTC/USD 1-hour bars for January 2026
uv run python -m src.cli.main fetch --source kraken --symbol BTC/USD --start 2026-01-01 --end 2026-01-31 --bar-type 1-HOUR-LAST
```

### Run backtest with Kraken data

```bash
# Run SMA crossover strategy against Kraken BTC/USD data
uv run python -m src.cli.main backtest --strategy sma_crossover --symbol BTC/USD --start 2026-01-01 --end 2026-01-31 --bar-type 1-HOUR-LAST --data-source kraken
```

### Via Web UI

1. Start web server: `uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000`
2. Navigate to backtest page
3. Select "Kraken" as data source
4. Enter crypto pair (e.g., BTC/USD) and date range

## Development

### Run tests

```bash
make test-unit          # Unit tests (mocked Kraken client)
make test-component     # Component tests (test doubles)
make test-integration   # Integration tests (real Kraken API, --forked)
```

### Key files

| File | Purpose |
|------|---------|
| `src/config.py` | KrakenSettings (env vars) |
| `src/services/kraken_client.py` | KrakenHistoricalClient |
| `src/services/data_catalog.py` | Extended with Kraken support |
| `src/services/exceptions.py` | KrakenConnectionError, KrakenRateLimitError |
| `tests/unit/services/test_kraken_client.py` | Unit tests |
| `tests/component/services/test_kraken_catalog.py` | Component tests |

## Known Limitations

- **Kraken Spot OHLC API**: Max 720 entries per request (most recent only). System uses Futures Charts API for deeper history.
- **Pair naming**: Kraken uses `XBT` for Bitcoin. System auto-maps `BTC` ↔ `XBT`.
- **24/7 markets**: Crypto trades continuously; no RTH (Regular Trading Hours) filtering applies.
