# Interactive Brokers TWS Setup Guide

## Prerequisites

- TWS (Trader Workstation) installed and running
- Paper trading account (DU prefix) recommended for testing
- Valid IBKR credentials in `.env` file

## Step 1: Enable API Connections in TWS

### Configure API Settings

1. Open TWS and log in to your account
2. Go to **File → Global Configuration → API → Settings**
3. Enable the following options:
   - ✅ **"Enable ActiveX and Socket Clients"**
   - ✅ **"Allow connections from localhost only"** (for security)
   - ✅ **"Read-Only API"** (recommended for data fetching)
4. Verify **Socket port** is set to:
   - **7497** for Paper Trading
   - **7496** for Live Trading
5. Set **Master API client ID** to **0** (allows any client ID)
6. Click **OK**

### Whitelist Localhost (if needed)

1. Go to **File → Global Configuration → API → Precautions**
2. Look for **"Trusted IP addresses"** section
3. If `127.0.0.1` is not listed, add it
4. Click **OK**

### Restart TWS

After making these changes, **restart TWS completely** for the settings to take effect.

## Step 2: Verify Your .env Configuration

Ensure your `.env` file has the correct settings:

```bash
# IBKR Connection Settings
IBKR_HOST=127.0.0.1
IBKR_PORT=7497              # 7497 for TWS paper trading
IBKR_CLIENT_ID=1            # Historical fetch. Rotates 1-6 on connect retry.
IBKR_LIVE_CLIENT_ID=10      # Live trading session. Must differ from IBKR_CLIENT_ID.
TRADING_MODE=paper          # Read under Docker (see note below)
IBKR_TRADING_MODE=paper     # Read on bare metal (see note below)

# IBKR Credentials
TWS_USERNAME=your_username
TWS_PASSWORD=your_password
TWS_ACCOUNT=DU1234567       # Your paper trading account

# Database
DATABASE_URL=postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader
```

> **Two variables, and which one the app reads depends on how you launch it.** Set both to the
> same value and you can ignore the rest of this note.
>
> - **Bare metal** (`uv run python -m src.cli.main`, `make web`): the app reads
>   **`IBKR_TRADING_MODE`** from `.env` — the `ibkr_trading_mode` field on `IBKRSettings`.
>   `TRADING_MODE` never reaches the app.
> - **Docker** (`docker compose up`): the reverse. Compose interpolates the host's
>   **`TRADING_MODE`** into the app container (`IBKR_TRADING_MODE: ${TRADING_MODE:-paper}`,
>   `docker-compose.yml:86`), and because that lands in the service's `environment:` block it
>   **outranks anything in `.env`** — the `ntrader-app` service has no `env_file:` directive and
>   the image never copies `.env`, so an `IBKR_TRADING_MODE` line in `.env` is inert there.
>   `TRADING_MODE` is also the ib-gateway container's own variable (`docker-compose.yml:50`), so
>   under Docker it is the single switch that flips both the gateway and the app.
>
> Setting them to *different* values is the trap: `TRADING_MODE=live` with
> `IBKR_TRADING_MODE=paper` gives you a **live** app under Docker and a **paper** app on bare
> metal, from the identical file.

### Client ID reservation

IBKR does not multiplex a client ID: a second connection presenting an ID already in use either
gets error 326 or **evicts the incumbent**. A live session sharing the catalog fetcher's ID would
be knocked off its socket mid-position by an ordinary historical import. The IDs are therefore
allocated, not picked at random:

| Consumer | Setting | ID | Effective range |
|---|---|---|---|
| Historical catalog fetch | `IBKR_CLIENT_ID` | `1` | **1–6** |
| Live trading session | `IBKR_LIVE_CLIENT_ID` | `10` | 10 |
| On-demand reconcile | `IBKR_LIVE_CLIENT_ID + 1` | `11` | 11 |

The historical client's range is wider than its configured ID because
`IBKRHistoricalClient.connect()` rotates through `base + 1 .. base + 5` when a connect attempt
times out (a SIGKILL'd run leaves the Gateway holding an ID for tens of seconds). **That rotation
is why the gap between 6 and 10 exists** — and why raising `IBKR_CLIENT_ID` to, say, `8` would
silently swallow both reserved IDs even though the two settings are not equal.

`IBKRSettings` rejects `IBKR_CLIENT_ID == IBKR_LIVE_CLIENT_ID` at construction, but it compares
configured bases only — it cannot see the rotation range. Keep `IBKR_CLIENT_ID` below the live
reservation.

## Step 3: Test Connection

Run the connection test command:

```bash
uv run python -m src.cli.main data connect
```

**Expected output:**
```
✅ Successfully connected to Interactive Brokers

Connection Details
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property       ┃ Value                   ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Host           │ 127.0.0.1               │
│ Port           │ 7497                    │
│ Account ID     │ DU1234567               │
│ Server Version │ 176                     │
│ Connection Time│ 2025-01-15 10:30:00     │
└────────────────┴─────────────────────────┘
```

## Step 4: Fetch Historical Data

Once connection is verified, fetch sample data:

```bash
# Fetch daily data for AAPL
uv run python -m src.cli.main data fetch \
  --instruments AAPL \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --timeframe DAILY
```

**Expected output:**
```
✅ AAPL: 21 bars fetched

✅ Successfully fetched 21 bars for 1 instruments

Fetch Summary
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property     ┃ Value                       ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Instruments  │ AAPL                        │
│ Start Date   │ 2024-01-01                  │
│ End Date     │ 2024-01-31                  │
│ Timeframe    │ DAILY                       │
│ Total Bars   │ 21                          │
│ Data Location│ ./data/catalog              │
└──────────────┴─────────────────────────────┘
```

## Troubleshooting

### Connection Refused (Error 502)

**Symptoms:**
```
[ERROR] Couldn't connect to TWS. Confirm that "Enable ActiveX and Socket EClients" is enabled
```

**Solutions:**
1. Verify TWS is running
2. Check API is enabled in TWS settings
3. Verify socket port matches (7497 for paper)
4. Restart TWS after changing settings
5. Check firewall isn't blocking localhost connections

### Client ID Conflict (Error 326)

**Symptoms:**
```
[ERROR] Client ID already in use
```

**Solutions:**
1. Change `IBKR_CLIENT_ID` in `.env` to a different number **below the live reservation** — `10`
   and `11` are taken (see [Client ID reservation](#client-id-reservation)), and the historical
   client auto-rotates five IDs above whatever base you choose, so stay at `4` or lower
2. Close any other API clients connected to TWS
3. Restart TWS

### Authentication Failed

**Symptoms:**
```
[ERROR] Authentication failed
```

**Solutions:**
1. Verify credentials in `.env` are correct
2. Ensure you're using paper trading account (DU prefix)
3. Check `IBKR_TRADING_MODE` is set to "paper" (not `TRADING_MODE` — see Step 2)

### Market Data Not Available

**Symptoms:**
```
[ERROR] No market data permissions
```

**Solutions:**
1. Paper trading accounts have delayed data by default
2. Set `ibkr_market_data_type="DELAYED_FROZEN"` in config
3. For real-time data, subscribe to market data in IBKR Account Management

## Advanced Features

### Fetch Multiple Instruments

```bash
uv run python -m src.cli.main data fetch \
  --instruments AAPL,MSFT,GOOGL \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --timeframe DAILY
```

### Different Timeframes

Available timeframes:
- `1-MINUTE` - Intraday 1-minute bars
- `5-MINUTE` - Intraday 5-minute bars
- `1-HOUR` - Intraday 1-hour bars
- `DAILY` - End-of-day bars

### Use with Backtest

After fetching data, use it in backtests:

```bash
# Fetch data
uv run python -m src.cli.main data fetch \
  --instruments AAPL \
  --start 2024-01-01 \
  --end 2024-12-31

# Run backtest with IBKR data
uv run python -m src.cli.main backtest run \
  --strategy sma \
  --symbols AAPL \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --data-source ibkr
```

## Rate Limiting

The system automatically rate-limits requests to **45 requests/second** (90% of IBKR's 50 req/sec limit) to prevent API throttling.

For large data fetches, the system will:
- Automatically throttle requests
- Show progress indicators
- Handle retries on temporary failures

## Security Notes

1. **Never commit `.env` file** - It contains your credentials
2. Use **Read-Only API** mode for data fetching
3. Keep **"Allow connections from localhost only"** enabled
4. Use paper trading account for testing
5. Monitor API usage in TWS Activity Log

## Support

For issues:
1. Check TWS API logs: **File → Log → Message** in TWS
2. Review application logs in `./logs/` directory
3. Consult IBKR API documentation: https://interactivebrokers.github.io/tws-api/

## Next Steps

Once you've successfully fetched data:
1. Verify data is stored in catalog: `ls ./data/catalog`
2. Import data to database (if needed)
3. Run backtests using IBKR data
4. Set up automated data fetching with cron/scheduler
