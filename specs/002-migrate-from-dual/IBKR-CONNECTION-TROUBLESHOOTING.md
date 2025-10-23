# IBKR Connection Troubleshooting Guide

## Issue Summary

Connection attempts to TWS on port 7497 are failing with:
```
Failed to receive server version information
ERROR: Couldn't connect to TWS (code: 502)
ERROR: Not connected (code: 504)
```

## Diagnosis

### ✅ What's Working
- Port 7497 is LISTENING (JavaAppli process PID 23402)
- TCP connections can be established
- Socket-level communication works

### ❌ What's Failing  
- TWS API handshake not completing
- Server version information not being sent by TWS
- API protocol-level communication failing

## Root Cause

The TWS API is not properly configured to accept socket connections. This is a **TWS configuration issue**, not a code issue.

## Solution Steps

### Step 1: Enable Socket Clients in TWS

1. Open TWS (Trader Workstation)
2. Go to: **File → Global Configuration** (or **Edit → Global Configuration** on some versions)
3. Navigate to: **API → Settings**
4. **Enable** the following checkbox:
   ```
   ☑ Enable ActiveX and Socket EClients
   ```
5. Verify "Socket Port" setting:
   ```
   Socket Port: 7497  (for paper trading)
   ```
6. Click **OK** or **Apply**
7. **Restart TWS** (important!)

### Step 2: Configure API Settings

While in **API → Settings**, also verify:

- ☑ **Read-Only API**: OFF (unchecked)
- ☑ **Download open orders on connection**: Optional
- ☑ **Allow connections from localhost only**: Recommended for security
- **Master API client ID**: 0 (default)
- **Socket Port**: 7497

### Step 3: Configure Master Client ID (If Needed)

If you have "Master API client ID" set to a specific value (not 0):
- Either set it to 0 (allow any client ID)
- OR use that specific client ID in your code:
  ```bash
  export IBKR_CLIENT_ID=<master-id>
  ```

### Step 4: Verify Trusted IPs (If Configured)

If you've configured "Trusted IPs":
1. Go to: **API → Settings → Trusted IPs**
2. Ensure `127.0.0.1` is in the list
3. OR remove all trusted IPs to allow localhost

### Step 5: Restart TWS

After making any changes:
1. **Close TWS completely**
2. **Reopen TWS**
3. **Log in** to your paper trading account
4. Wait for TWS to fully initialize

### Step 6: Test Connection

```bash
# Test with default client ID (10)
export IBKR_PORT=7497
uv run python -m src.cli.main data connect

# OR test with different client ID
export IBKR_PORT=7497 IBKR_CLIENT_ID=15
uv run python -m src.cli.main data connect
```

## Common Issues

### Issue: "Failed to receive server version information"
**Cause**: TWS API not enabled  
**Solution**: Follow Step 1 above

### Issue: "Connection refused" (error 111)
**Cause**: TWS not running or wrong port  
**Solution**: 
- Verify TWS is running
- Check port setting (7497 for paper, 7496 for live)
- Try: `lsof -i :7497`

### Issue: "Client ID already in use"
**Cause**: Previous connection still active  
**Solution**:
```bash
# Kill stale Python connections
ps aux | grep python | grep -i ibkr
kill -9 <PID>

# Try different client ID
export IBKR_CLIENT_ID=15
```

### Issue: Works from TWS machine but not remotely
**Cause**: Localhost-only restriction  
**Solution**: 
- In TWS API settings, uncheck "Allow connections from localhost only"
- Add your IP to "Trusted IPs"

## Port Reference

| Environment | TWS Port | Gateway Port |
|-------------|----------|--------------|
| Paper Trading (Sim) | 7497 | 4002 |
| Live Trading | 7496 | 4001 |

## Verification Commands

### Check if TWS is listening:
```bash
lsof -i :7497
# Should show: JavaAppli ... *:7497 (LISTEN)
```

### Check for established connections:
```bash
lsof -i :7497 | grep ESTABLISHED
# Should show active Python connections
```

### Kill stale connections:
```bash
# Find Python processes connected to 7497
lsof -i :7497 | grep python | awk '{print $2}' | xargs kill -9
```

## Alternative: IB Gateway

If TWS continues to have issues, consider using IB Gateway instead:

1. Download IB Gateway from IBKR website
2. Install and configure for paper trading
3. Use port 4002 (Gateway paper trading port):
   ```bash
   export IBKR_PORT=4002
   ```
4. Gateway is lighter weight and often more stable for API connections

## Testing Without IBKR Connection

**Good News**: The backtesting system works perfectly with cached data!

```bash
# Test with existing cached data (no IBKR needed)
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2023-12-29 20:01:00" \
  --end "2023-12-29 21:00:00" \
  --fast-period 5 \
  --slow-period 10

# Result: 0.04s execution time! ⚡
```

## Additional Resources

- [IBKR API Documentation](https://interactivebrokers.github.io/tws-api/)
- [TWS API Getting Started](https://interactivebrokers.github.io/tws-api/initial_setup.html)
- [Socket Client Configuration](https://interactivebrokers.github.io/tws-api/initial_setup.html#enable_api)

## Support

If issues persist after following this guide:
1. Check IBKR API logs: `<TWS-install-dir>/logs/`
2. Verify TWS version is up to date
3. Contact IBKR support for API access issues
4. Check IBKR API forums for similar issues

---

*Last Updated: 2025-10-16*  
*Status: TWS Configuration Required*
