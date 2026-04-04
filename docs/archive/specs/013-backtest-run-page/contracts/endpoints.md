# Endpoint Contracts: Backtest Run Page

**Date**: 2026-03-20
**Feature**: 013-backtest-run-page

## UI Routes (HTML Responses)

### GET /backtests/run

**Purpose**: Display the backtest configuration form.

**Response**: HTML page with form containing strategy dropdown, symbol input, date pickers, data source selector, timeframe selector, starting balance, timeout, and submit button.

**Template context**:
- `strategies`: List of `StrategyOption` (name, description)
- `data_sources`: List of available data source strings
- `timeframes`: List of available timeframe strings
- `defaults`: Default values for optional fields
- `nav_state`: NavigationState with `active_page="run_backtest"`
- `errors`: Optional dict of field-level validation errors (on re-render after validation failure)
- `form_data`: Optional dict of previously submitted values (for re-population on error)

---

### GET /backtests/run/strategy-params/{strategy_name}

**Purpose**: Return HTMX fragment with strategy-specific parameter inputs.

**Path params**:
- `strategy_name`: Registered strategy name (e.g., "sma_crossover")

**Response**: HTML fragment containing labeled input fields for the strategy's parameters, with defaults pre-filled.

**Error**: Returns empty fragment if strategy not found (graceful degradation).

---

### POST /backtests/run

**Purpose**: Submit backtest configuration, execute the backtest, and redirect to results.

**Request body** (form-encoded):
- `strategy`: str (required)
- `symbol`: str (required)
- `start_date`: str, YYYY-MM-DD (required)
- `end_date`: str, YYYY-MM-DD (required)
- `data_source`: str (required, default "catalog")
- `timeframe`: str (required, default "1-DAY")
- `starting_balance`: str/float (optional, default 1000000)
- `timeout_seconds`: str/int (optional, default 300)
- Additional strategy-specific params as `param_{name}` fields

**Success response** (HTMX):
- Header: `HX-Redirect: /backtests/{run_id}`

**Validation error response**:
- Re-rendered form with `errors` dict and `form_data` populated

**Execution error response**:
- HTML fragment with error message, swapped into the result area

**Timeout error response**:
- HTML fragment with timeout-specific error message and suggestions

**Concurrent execution response**:
- HTTP 409 Conflict with "backtest already in progress" message
