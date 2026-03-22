---
name: e2e-ui-testing
description: End-to-end UI testing for the NTrader backtesting web application. Use when verifying user workflows through the browser — running backtests, viewing results, navigating pages, checking form validation, or confirming HTMX interactions work correctly. Triggers include "test the UI", "verify the backtest page", "check the form works", "e2e test", or any request to validate web UI behavior in a real browser.
allowed-tools: Bash(npx agent-browser:*), Bash(agent-browser:*), mcp__playwright__*
---

# E2E UI Testing — NTrader Backtesting Application

Test the NTrader web UI end-to-end using **Playwright MCP tools** (primary) and **agent-browser CLI** (alternative). The app is a FastAPI + HTMX + Jinja2 server-rendered application with Tailwind CSS styling.

## Quick Reference

| What | Value |
|------|-------|
| Dev server | `http://127.0.0.1:8000` |
| Start command | `uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000` |
| CSS build (first time) | `./scripts/build-css.sh` |
| Key pages | `/` (dashboard), `/backtests` (list), `/backtests/run` (form), `/backtests/{id}` (detail), `/docs` (Swagger), `/data` (data mgmt) |

## Before You Start

### 1. Ensure the dev server is running

```bash
# Check if already running
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo "NOT RUNNING"
```

If not running, start it in the background:
```bash
uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000 &
```

### 2. Ensure CSS is built
```bash
./scripts/build-css.sh
```

### 3. Database must be migrated
```bash
uv run alembic upgrade head
```

## Testing Tools — When to Use Which

### Playwright MCP (preferred for most tasks)
Use Playwright MCP tools (`mcp__playwright__*`) for structured interaction:
- `mcp__playwright__browser_navigate` — go to a URL
- `mcp__playwright__browser_snapshot` — get accessibility tree with element refs
- `mcp__playwright__browser_click` — click an element
- `mcp__playwright__browser_fill_form` — fill a form field
- `mcp__playwright__browser_select_option` — select dropdown option
- `mcp__playwright__browser_wait_for` — wait for text/element
- `mcp__playwright__browser_take_screenshot` — capture evidence
- `mcp__playwright__browser_evaluate` — run JavaScript in the page
- `mcp__playwright__browser_network_requests` — inspect XHR/fetch calls
- `mcp__playwright__browser_console_messages` — check for JS errors

### agent-browser CLI (alternative, good for chained commands)
Use when you need command chaining or batch operations:
```bash
agent-browser open http://127.0.0.1:8000/backtests/run && agent-browser wait --load networkidle && agent-browser snapshot -i
```

## Core Testing Workflow

Every E2E test follows this pattern:

```
1. Navigate to page (snapshot returned automatically)
2. Assert page loaded correctly (check for expected text/elements in returned snapshot)
3. Interact (fill forms, click buttons — click also returns a snapshot)
4. Wait for response (HTMX swap, navigation)
5. Re-snapshot (only needed after HTMX partial swaps or wait_for — navigate/click already return snapshots)
6. Assert expected outcome
7. Screenshot for evidence
```

**Key insight**: `browser_navigate` and `browser_click` both return the page snapshot in their response. You only need a separate `browser_snapshot` call after HTMX partial updates or `wait_for` calls.

## Playwright MCP Parameter Reference

These are the actual parameter names — use these exactly:

| Tool | Key Parameters |
|------|---------------|
| `browser_navigate` | `url` (string) |
| `browser_snapshot` | (no required params) |
| `browser_click` | `ref` (string), `element` (string, description) |
| `browser_fill_form` | `fields` (array of `{name, type, ref, value}`) — type is one of: `textbox`, `checkbox`, `radio`, `combobox`, `slider` |
| `browser_select_option` | `ref` (string), `values` (array of strings — option **values**, not display text), `element` (string) |
| `browser_wait_for` | `text` (string), `textGone` (string), `time` (number, seconds) — **no** `timeout` or `url` params |
| `browser_evaluate` | `function` (string, arrow function syntax), optionally `ref` + `element` for element context |
| `browser_take_screenshot` | `type` ("png"/"jpeg"), `filename` (string), `fullPage` (bool) |
| `browser_console_messages` | `level` ("error"/"warning"/"info"/"debug") |

## Critical HTMX Pitfalls

This application uses HTMX heavily. These are the most common failure modes:

### 1. HTMX form submission — click() may not trigger HTMX

**Problem**: Playwright's `click` on a submit button may trigger native form submission instead of HTMX's `hx-post` handler, causing a full page reload instead of an HTMX swap.

**Solution**: Use JavaScript evaluation to trigger the click, ensuring HTMX intercepts:
```
mcp__playwright__browser_evaluate
  function: "() => document.querySelector('button[type=\"submit\"]').click()"
```

Or with agent-browser:
```bash
agent-browser eval "document.querySelector('button[type=\"submit\"]').click()"
```

### 2. HTMX swaps invalidate element refs

After any HTMX request completes (partial page update), **all element refs become stale**. You MUST re-snapshot before interacting with any element.

```
# BAD — refs from before HTMX swap
mcp__playwright__browser_snapshot  # get refs
mcp__playwright__browser_select_option ref="@e3" value="SMA Crossover"  # triggers hx-get
mcp__playwright__browser_click ref="@e7"  # STALE REF — will fail or click wrong element

# GOOD — re-snapshot after HTMX swap
mcp__playwright__browser_snapshot  # get refs
mcp__playwright__browser_select_option ref="@e3" value="SMA Crossover"  # triggers hx-get
mcp__playwright__browser_wait_for text="Fast Period"  # wait for strategy params to load
mcp__playwright__browser_snapshot  # FRESH refs after DOM update
mcp__playwright__browser_click ref="@e7"  # now safe
```

### 3. HTMX indicator elements are hidden by default

The `htmx-indicator` class uses `opacity: 0` by default and `opacity: 1` during requests. Don't assert on spinner visibility unless you're testing the in-flight state.

### 4. hx-disabled-elt disables buttons during requests

The submit button gets `disabled` during HTMX requests. If a backtest takes a long time, don't try to click submit again — wait for the response.

## Date Input Handling

**`<input type="date">` fields are NOT simple text inputs.** Browsers render them as compound controls (3 spinbuttons for month/day/year). Standard `fill` commands silently fail.

**Solution**: Use JavaScript with native value setter + event dispatch:

```
mcp__playwright__browser_evaluate
  function: |
    () => {
      const input = document.querySelector('#start_date');
      const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      nativeSetter.call(input, '2024-01-01');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
```

Or with agent-browser:
```bash
agent-browser eval <<'JS'
const input = document.querySelector('#start_date');
const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
nativeSetter.call(input, '2024-01-01');
input.dispatchEvent(new Event('input', { bubbles: true }));
input.dispatchEvent(new Event('change', { bubbles: true }));
JS
```

**Always use this pattern for `#start_date` and `#end_date` fields.**

## Select Dropdowns

The strategy selector, data source, and timeframe fields are native `<select>` elements. Use `select_option` with the `values` parameter (an **array** of option `value` attributes, not display text):

```
mcp__playwright__browser_select_option ref="e32" element="Strategy dropdown" values=["sma_crossover"]
```

Available strategy values: `sma_crossover`, `momentum`, `mean_reversion`, `bollinger_reversal`, `sma_crossover_long_only`, `connors_rsi_mean_rev`, `apolo_rsi`

**After selecting a strategy**, the form fires `hx-get` to load dynamic strategy parameters. Wait for the params to appear before continuing:
```
mcp__playwright__browser_wait_for text="Fast Period"
```

## Timeouts for Long Operations

Backtest execution can take 30-120+ seconds. The Playwright MCP is configured with:
- Action timeout: 60s
- Navigation timeout: 120s

For agent-browser, set the timeout environment variable:
```bash
AGENT_BROWSER_DEFAULT_TIMEOUT=120000 agent-browser click @submitRef
```

When waiting for backtest completion, use `wait_for` with expected result text rather than fixed sleeps. Note: `wait_for` only supports `text`, `textGone`, and `time` parameters — there is no `timeout` or `url` parameter:
```
mcp__playwright__browser_wait_for text="SUCCESS"
```

If you need to wait longer than the default timeout, use `time` to wait a fixed duration as a fallback:
```
mcp__playwright__browser_wait_for time=120
```

## Page-Specific Testing Guides

### Dashboard (`/`)
- Verify page loads with navigation bar
- Check for summary statistics (if backtests exist)
- Verify links to backtest list and run pages

### Backtest List (`/backtests`)
- Verify table renders with 9 column headers: Run ID, Strategy, Symbol, Date Range, Return, Sharpe, Max DD, Status, Created
- **Filters** (all trigger HTMX partial updates): Strategy dropdown, Instrument dropdown, Status dropdown (All/Success/Failed), From Date, To Date, Clear Filters button
- **Sortable columns**: Return, Sharpe, Max DD, Created (click header button to sort)
- **Pagination**: Previous/Next buttons, "Page X of Y" indicator
- Verify clicking a row navigates to detail page (`/backtests/{run_id}`)

### Backtest Run Form (`/backtests/run`)
Full form fields:
| Field | Type | Selector | Notes |
|-------|------|----------|-------|
| Strategy | `<select>` | `#strategy` | Triggers HTMX param load on change |
| Symbol | `<input text>` | `#symbol` | e.g., "AAPL" or "AAPL.NASDAQ" |
| Start Date | `<input date>` | `#start_date` | Use JS setter pattern |
| End Date | `<input date>` | `#end_date` | Use JS setter pattern |
| Data Source | `<select>` | `#data_source` | Default: "catalog" |
| Timeframe | `<select>` | `#timeframe` | Default: "1-DAY" |
| Starting Balance | `<input number>` | `#starting_balance` | Default: 1000000 |
| Timeout | `<input number>` | `#timeout_seconds` | Default: 300 |
| Strategy Params | dynamic | `#strategy-params` | Loaded via HTMX after strategy selection |
| Submit | `<button>` | `button[type="submit"]` | Use JS click pattern |

**Form submission flow**:
1. Fill all fields
2. Click submit (via JS eval)
3. Spinner appears (`#backtest-spinner` gains visibility)
4. Button becomes disabled
5. On success: redirects to detail page (`/backtests/{run_id}`)
6. On error: re-renders form with error message in red banner

### Backtest Detail (`/backtests/{run_id}`)

**Successful backtest** shows these sections (in order):
- Header: Run ID, strategy name, execution timestamp, duration, SUCCESS badge
- **Performance Metrics**: Returns (Total Return, CAGR, Final Balance), Risk (Sharpe, Sortino, Max DD, Volatility), Trading (Total Trades, Win Rate, Profit Factor)
- **Trading Summary**: Total/Winning/Losing trades, Win Rate, Avg Win/Loss, Profit Factor, Expectancy
- **Charts**: Price Chart and Equity Curve (TradingView lightweight-charts)
- **Trade Statistics**: Trade Counts, Performance, Profit/Loss, Streaks & Time
- **Drawdown Analysis**: Maximum drawdown details, top drawdown periods
- **Individual Trades**: Paginated table with Entry/Exit dates, Symbol, Direction, Quantity, Prices, P&L
- **Configuration Parameters** (collapsible `<details>`): Instrument, Date Range, Capital, Strategy params, CLI command with Copy button
- Action buttons: Export Report, Export Trades (CSV/JSON), Delete, Re-run Backtest

**Failed backtest** shows:
- Header with FAILED badge
- Error message in red banner (the actual error text, e.g., "invalid bar.volume.precision=0...")
- Action buttons (Export, Delete, Re-run)
- Configuration Parameters (collapsible)
- No metrics, charts, or trade sections

## Assertion Patterns

### Check page loaded (navigate/click return snapshots automatically)
```
# The snapshot is already in the navigate/click response — check for expected text
# e.g., after navigate to /, look for "Dashboard" heading and "NTrader" in nav
```

### Check for error messages
```
# Accessibility snapshots show TEXT content, not CSS classes
# Look for the actual error message string, e.g., "invalid bar.volume.precision"
# or status text like "FAILED" in the snapshot output
```

### Check navigation occurred
```
# After clicking a link — the click response includes the new page URL and snapshot
# Verify the Page URL in the response matches expected destination
# No separate wait_for needed — click blocks until navigation completes
```

### Check HTMX partial update
```
# After triggering an HTMX request (e.g., select_option that fires hx-get):
mcp__playwright__browser_wait_for text="Expected new content"
mcp__playwright__browser_snapshot  # get fresh refs for further interaction
```

### Capture evidence
```
mcp__playwright__browser_take_screenshot type="png" filename="test-evidence.png"
# Screenshot is returned inline — describe what it shows
```

### Check console for errors
```
mcp__playwright__browser_console_messages level="error"
# Returns count and content of console errors
```

## Common Test Scenarios

### Smoke test — page loads
```
Navigate to / → snapshot → verify "NTrader" in nav
Navigate to /backtests → snapshot → verify table or "No backtests" message
Navigate to /backtests/run → snapshot → verify form fields present
```

### Form validation — empty submit
```
Navigate to /backtests/run → snapshot
Submit form with empty required fields (via JS click)
Wait for response → snapshot
Verify validation error messages appear
```

### Happy path — run a backtest
```
Navigate to /backtests/run → snapshot
Select strategy → wait for params → re-snapshot
Fill symbol, dates (JS setter), balance
Submit (JS click) → wait for redirect (up to 120s)
Snapshot detail page → verify metrics
Screenshot for evidence
```

### Navigation flow
```
Dashboard → click "Backtests" link → verify list page
List page → click a backtest row → verify detail page
Detail page → click "Back" → verify list page
Detail page → click "Rerun" → verify run form pre-filled
```

## Debugging Failed Tests

1. **Screenshot**: Always take a screenshot when something unexpected happens
2. **Console messages**: Check `mcp__playwright__browser_console_messages` for JS errors
3. **Network requests**: Use `mcp__playwright__browser_network_requests` to see if API calls failed
4. **HTMX errors**: HTMX logs to console — check for `htmx:responseError` or `htmx:swapError`
5. **Server logs**: Check the uvicorn terminal output for Python tracebacks

## Anti-Patterns

- **Never guess element refs** — always snapshot first
- **Never reuse refs after navigation or HTMX swap** — always re-snapshot
- **Never use `fill` on date inputs** — always use JS native setter
- **Never use fixed `sleep`** — always use `wait_for` with a condition
- **Never assume the server is running** — check first with curl
- **Never click submit directly for HTMX forms** — use JS eval to ensure HTMX intercepts
- **Never assume a backtest will complete quickly** — use 120s+ timeouts
- **Never test against production data assumptions** — check what strategies/data are actually available
