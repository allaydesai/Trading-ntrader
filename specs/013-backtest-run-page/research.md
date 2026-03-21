# Research: Backtest Run Page

**Date**: 2026-03-20
**Feature**: 013-backtest-run-page

## Decision 1: How to invoke backtest execution from the web layer

**Decision**: Reuse `BacktestRequest.from_cli_args()` + `load_backtest_data()` + `BacktestOrchestrator.execute()` — the same pipeline the CLI uses.

**Rationale**: The CLI already orchestrates the full flow: resolve request → load data → execute → persist. The `BacktestRequest` model, `load_backtest_data()` helper, and `BacktestOrchestrator` are all async-compatible and decoupled from CLI concerns. Reusing them avoids duplicating logic and ensures web-launched backtests produce identical results to CLI-launched ones.

**Alternatives considered**:
- Calling CLI as a subprocess: Rejected — adds process overhead, loses error context, and makes timeout control harder.
- Duplicating the orchestration logic in the web layer: Rejected — violates DRY and risks divergence.

## Decision 2: Dynamic strategy parameter form rendering

**Decision**: Use `StrategyRegistry.get(name).param_model.model_json_schema()` to generate form fields dynamically via an HTMX partial endpoint.

**Rationale**: Each strategy registers a Pydantic `param_model` with typed fields, defaults, and validation constraints (e.g., `ge`, `le`, `gt`). Pydantic's `model_json_schema()` provides field names, types, defaults, and constraints in a standard format. An HTMX endpoint can return rendered HTML inputs for the selected strategy, keeping the form server-rendered and consistent with the existing HTMX pattern.

**Alternatives considered**:
- Client-side JavaScript form generation from a JSON schema endpoint: Rejected — adds JS complexity, inconsistent with existing server-rendered HTMX approach.
- Hardcoded forms per strategy: Rejected — doesn't scale as new strategies are added.

## Decision 3: Backtest execution timeout implementation

**Decision**: Use `asyncio.wait_for()` wrapping the orchestrator execution call, with a user-configurable timeout field defaulting to 300 seconds (5 minutes).

**Rationale**: `asyncio.wait_for()` is the standard async timeout mechanism. It raises `asyncio.TimeoutError` which can be caught and translated to a user-friendly error. The 5-minute default is generous for most backtests while preventing indefinite hangs.

**Alternatives considered**:
- Thread-based timeout with `concurrent.futures`: Rejected — more complex, doesn't integrate well with async flow.
- No timeout (rely on user patience): Rejected — clarification explicitly chose user-configurable timeout.

## Decision 4: Concurrent execution prevention

**Decision**: Use a module-level `asyncio.Lock` in the run backtest route module. The POST handler acquires the lock with `try_lock` semantics — if already held, return a 409 Conflict with "backtest already in progress" message.

**Rationale**: The app is single-process (uvicorn with reload for dev). A simple asyncio lock prevents concurrent execution without external infrastructure. The lock is released after execution completes or fails.

**Alternatives considered**:
- Database-level lock (advisory lock): Rejected — over-engineered for single-user tool.
- Redis lock: Rejected — adds dependency for simple use case.

## Decision 5: Progress indicator approach

**Decision**: Use HTMX `hx-indicator` with a CSS spinner overlay. The form POST targets a swap area; while waiting for the response, HTMX shows the indicator automatically.

**Rationale**: HTMX's built-in `hx-indicator` pattern requires zero custom JavaScript. The backtest runs synchronously on the server; HTMX waits for the response while showing the indicator. On success, the response is a redirect header (`HX-Redirect`); on failure, the swap area receives the error HTML.

**Alternatives considered**:
- Server-Sent Events for real-time progress: Rejected — adds complexity; the backtest doesn't emit granular progress events.
- Polling endpoint: Rejected — requires async job queue infrastructure, explicitly out of scope.

## Decision 6: Route structure

**Decision**: Add routes directly to the existing `src/api/ui/backtests.py` module rather than creating a new file.

**Rationale**: The "Run Backtest" page is part of the backtests domain. Adding 2-3 routes (GET form, POST submit, GET strategy params fragment) to the existing backtests router keeps related routes together. The file is currently ~200 lines, well within the 500-line limit even with additions.

**Alternatives considered**:
- Separate `src/api/ui/run_backtest.py` module: Acceptable but unnecessary — the routes logically belong with other backtest UI routes.
