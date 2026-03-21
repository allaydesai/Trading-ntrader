# Data Model: Backtest Run Page

**Date**: 2026-03-20
**Feature**: 013-backtest-run-page

## Entities

### BacktestRunFormData (new — web form input model)

Captures the form submission data before it's transformed into a `BacktestRequest`.

| Field | Type | Required | Default | Constraints |
|-------|------|----------|---------|-------------|
| strategy | str | yes | — | Must match a registered strategy name |
| symbol | str | yes | — | Non-empty, free-text (e.g., "AAPL", "BTC/USD") |
| start_date | date | yes | — | Must be before end_date |
| end_date | date | yes | — | Must be after start_date |
| data_source | str | yes | "catalog" | One of: catalog, ibkr, kraken, mock |
| timeframe | str | yes | "1-DAY" | One of: 1-MINUTE, 5-MINUTE, 15-MINUTE, 1-HOUR, 4-HOUR, 1-DAY, 1-WEEK |
| starting_balance | Decimal | no | 1000000 | Must be > 0 |
| timeout_seconds | int | no | 300 | Must be > 0 |
| strategy_params | dict[str, Any] | no | {} | Validated against strategy's param_model |

**Relationships**:
- Transforms into existing `BacktestRequest` via `BacktestRequest.from_cli_args()`
- Strategy params validated against `StrategyRegistry.get(strategy).param_model`

### StrategyOption (new — view model for dropdown)

Represents a strategy choice in the form dropdown.

| Field | Type | Source |
|-------|------|--------|
| name | str | StrategyRegistry.list_strategies() |
| description | str | StrategyRegistry.list_strategies() |
| aliases | list[str] | StrategyRegistry.list_strategies() |

### StrategyParamField (new — view model for dynamic form fields)

Represents a single parameter field for a strategy, derived from the param_model's JSON schema.

| Field | Type | Source |
|-------|------|--------|
| name | str | param_model.model_json_schema()["properties"] key |
| field_type | str | JSON schema "type" (integer, number, string, boolean) |
| default | Any | JSON schema "default" |
| description | str | JSON schema "description" or "title" |
| minimum | Optional[number] | JSON schema "minimum" or "exclusiveMinimum" |
| maximum | Optional[number] | JSON schema "maximum" or "exclusiveMaximum" |
| required | bool | From JSON schema "required" list |

## Existing Entities (no changes needed)

- **BacktestRequest** (`src/models/backtest_request.py`): Already has `from_cli_args()` factory method that accepts all needed parameters.
- **BacktestRun** (`src/db/models/backtest.py`): Database model for persisted results. No schema changes needed.
- **PerformanceMetrics** (`src/db/models/backtest.py`): Related metrics model. No changes needed.
- **StrategyDefinition** (`src/core/strategy_registry.py`): Registry entry with `param_model`. No changes needed.

## State Transitions

### Form Submission Flow

```
IDLE → SUBMITTING → SUCCESS (redirect) | ERROR (show message)
```

- **IDLE**: Form is visible, submit button enabled
- **SUBMITTING**: Form disabled, spinner shown, backtest executing on server
- **SUCCESS**: Server returns HX-Redirect to `/backtests/{run_id}`
- **ERROR**: Server returns error HTML swapped into the form area

### Execution Lock State

```
UNLOCKED → LOCKED (backtest running) → UNLOCKED (complete/failed/timeout)
```

Only one transition at a time. Lock is per-process (asyncio.Lock).
