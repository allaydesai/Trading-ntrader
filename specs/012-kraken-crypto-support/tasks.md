# Tasks: Kraken Crypto Data Support

**Input**: Design documents from `/specs/012-kraken-crypto-support/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — constitution mandates TDD (Red-Green-Refactor) for all implementation.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Install dependency and establish green baseline

- [ ] T001 Install python-kraken-sdk dependency via `uv add python-kraken-sdk`
- [ ] T002 Run existing test suite (`make test-all`) to verify green baseline before changes

**Checkpoint**: Dependency installed, all existing tests pass

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Exception types and configuration that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 [P] Add `KrakenConnectionError` and `KrakenRateLimitError` exception classes (inheriting from `CatalogError`) in `src/services/exceptions.py`. `KrakenRateLimitError` must include `retry_after: int` attribute. Follow the existing `IBKRConnectionError` and `RateLimitExceededError` patterns.
- [ ] T004 [P] Write failing unit tests for `KrakenSettings` in `tests/unit/services/test_kraken_settings.py`. Test cases: (1) default values load correctly, (2) env var overrides work, (3) `kraken_api_key` and `kraken_api_secret` must both be set or both empty, (4) `kraken_rate_limit` validates range 1-20, (5) fee values validate range 0-1. Reference `data-model.md` KrakenSettings entity for fields.
- [ ] T005 Implement `KrakenSettings` Pydantic `BaseSettings` class in `src/config.py` and nest as `kraken: KrakenSettings = Field(default_factory=KrakenSettings)` in the `Settings` class. Fields: `kraken_api_key` (str, default ""), `kraken_api_secret` (str, default ""), `kraken_rate_limit` (int, default 10), `kraken_default_maker_fee` (Decimal, default 0.0016), `kraken_default_taker_fee` (Decimal, default 0.0026). Add validators per data-model.md. Must make T004 tests pass.
- [ ] T006 Run unit tests (`make test-unit`) to verify foundational phase passes

**Checkpoint**: Foundation ready — KrakenSettings validated, exceptions defined, user story work can begin

---

## Phase 3: User Story 1 — Fetch Historical Crypto Data from Kraken (Priority: P1) MVP

**Goal**: Fetch OHLCV bar data from Kraken for any spot pair, convert to Nautilus format, persist in Parquet catalog with caching and gap detection.

**Independent Test**: Request BTC/USD 1-hour bars for a date range → bars stored in local catalog → subsequent request serves from cache.

### Tests for User Story 1

> **Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T007 [P] [US1] Write failing unit tests for pair name mapping in `tests/unit/services/test_kraken_client.py`. Test: (1) `BTC/USD` → Nautilus ID `BTC/USD.KRAKEN`, Kraken REST `XXBTZUSD`, Charts symbol `XBT/USD`, (2) `ETH/USD` → `ETH/USD.KRAKEN`, `XETHZUSD`, `ETH/USD`, (3) `SOL/USD` → `SOL/USD.KRAKEN`, `SOLUSD`, `SOL/USD`, (4) BTC↔XBT and DOGE↔XDG special mappings, (5) unknown pair raises descriptive error. Reference `research.md` R-003 mapping table.
- [ ] T008 [P] [US1] Write failing unit tests for OHLCV-to-Bar conversion in `tests/unit/services/test_kraken_client.py`. Test: (1) Futures Charts API response `{time, open, high, low, close, volume}` converts to Nautilus `Bar` with correct BarType `BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL`, (2) timestamps convert from milliseconds to nanoseconds, (3) string prices convert via `Price.from_str()`, (4) `CurrencyPair` instrument constructed with correct precision/fees from asset pair metadata. Reference `research.md` R-005 mapping table and `data-model.md` KrakenOHLCVRecord.
- [ ] T009 [P] [US1] Write failing unit tests for `KrakenHistoricalClient.fetch_bars()` in `tests/unit/services/test_kraken_client.py`. Mock Kraken API responses. Test: (1) successful fetch returns `(list[Bar], CurrencyPair)` tuple, (2) pagination handles `more_candles: true` with multiple requests, (3) `KrakenConnectionError` raised on API failure, (4) `DataNotFoundError` raised for unsupported pair, (5) bar_type_spec mapping (`1-MINUTE-LAST` → `1m`, `1-HOUR-LAST` → `1h`, `1-DAY-LAST` → `1d`). Reference `contracts/kraken-client.md` interface.
- [ ] T010 [P] [US1] Write failing unit tests for rate limiter in `tests/unit/services/test_kraken_client.py`. Test: (1) requests within limit proceed immediately, (2) requests exceeding limit are delayed, (3) sliding window resets after decay period, (4) configurable rate from `KrakenSettings.kraken_rate_limit`. Reuse pattern from existing `RateLimiter` in `src/services/ibkr_client.py`.

### Implementation for User Story 1

- [ ] T011 [US1] Implement pair mapping module in `src/services/kraken_client.py`: `KrakenPairMapper` class with methods `to_nautilus_id(kraken_pair) -> str`, `to_kraken_rest(user_pair) -> str`, `to_kraken_charts(user_pair) -> str`, and `from_nautilus_id(nautilus_id) -> tuple[str, str]`. Include `SYMBOL_MAP = {"BTC": "XBT", "DOGE": "XDG"}` and reverse mapping. Must fetch and cache Kraken's `/0/public/AssetPairs` for dynamic resolution. Make T007 tests pass.
- [ ] T012 [US1] Implement OHLCV-to-Bar converter and `CurrencyPair` instrument factory in `src/services/kraken_client.py`: function `convert_ohlcv_to_bars(candles, instrument_id, bar_type_spec, price_precision) -> list[Bar]` and function `build_currency_pair(pair_info, maker_fee, taker_fee) -> CurrencyPair`. BarType format: `{instrument_id}-{bar_type_spec}-EXTERNAL`. Timestamps: milliseconds → nanoseconds. Make T008 tests pass.
- [ ] T013 [US1] Implement `KrakenHistoricalClient` class in `src/services/kraken_client.py` following `contracts/kraken-client.md` interface. Constructor accepts `api_key`, `api_secret`, `rate_limit`, `default_maker_fee`, `default_taker_fee`. Methods: `async connect()` (init `SpotAsyncClient` + validate), `async disconnect()`, `async fetch_bars()` (resolve pair → fetch via Futures Charts API `GET /api/charts/v1/spot/{symbol}/{resolution}?from=X&to=Y` with `more_candles` pagination → convert to Bars + instrument), `async fetch_asset_pairs()`, `is_connected` property. Use `kraken.futures.FuturesClient` for charts data, `kraken.spot.SpotAsyncClient` for asset pair metadata. Make T009 tests pass.
- [ ] T014 [US1] Implement `KrakenRateLimiter` in `src/services/kraken_client.py`: sliding window rate limiter with `async acquire()` method, `asyncio.Lock` for thread safety, configurable `requests_per_second` from settings. Follow existing `RateLimiter` pattern in `src/services/ibkr_client.py`. Integrate into `KrakenHistoricalClient.fetch_bars()`. Make T010 tests pass.
- [ ] T015 [US1] Write failing component test for `DataCatalogService` with Kraken client in `tests/component/services/test_kraken_catalog.py`. Test: (1) `fetch_or_load(data_source="kraken")` calls `kraken_client.fetch_bars()` and writes to catalog, (2) second call for same range serves from cache (no API call), (3) gap detection works for Kraken data, (4) `data_source="ibkr"` still uses IBKR client (backward compatibility). Use mocked `KrakenHistoricalClient`.
- [ ] T016 [US1] Extend `DataCatalogService` in `src/services/data_catalog.py`: add `kraken_client: KrakenHistoricalClient | None = None` constructor param, add lazy `kraken_client` property (same pattern as `ibkr_client`), add `data_source: str = "ibkr"` parameter to `fetch_or_load()` method that routes to appropriate client. Default `"ibkr"` preserves backward compatibility. Follow `contracts/data-catalog-extension.md`. Make T015 tests pass.

**Checkpoint**: User Story 1 fully functional — can fetch Kraken OHLCV data, convert to Nautilus bars, store in Parquet catalog, serve from cache. Run `make test-unit && make test-component` to verify.

---

## Phase 4: User Story 2 — Run Backtests with Kraken Crypto Data (Priority: P2)

**Goal**: Execute backtests against Kraken crypto data using existing strategies, accessible via CLI and web UI.

**Independent Test**: Run SMA crossover strategy against BTC/USD Kraken data → backtest completes → produces trades, metrics, equity curve.

**Dependencies**: Requires US1 (data fetching) to be complete.

### Tests for User Story 2

- [ ] T017 [US2] Write failing integration test for end-to-end Kraken backtest in `tests/integration/test_kraken_backtest.py`. Test: (1) create `BacktestRequest` with `data_source="kraken"`, BTC/USD symbol, (2) fetch data via `DataCatalogService.fetch_or_load(data_source="kraken")`, (3) run backtest via `BacktestOrchestrator.execute()`, (4) verify `BacktestResult` contains trades and metrics. Use mocked Kraken API responses for reproducibility. Mark with `@pytest.mark.integration`.

### Implementation for User Story 2

- [ ] T018 [P] [US2] Update `BacktestRequest` in `src/models/backtest_request.py`: update `data_source` field description to document `"kraken"` as a valid option alongside `"catalog"`, `"ibkr"`, and `"mock"`. Add field validator to validate allowed data_source values.
- [ ] T019 [P] [US2] Add `--data-source` option to CLI backtest command in `src/cli/main.py`. Add `click.Choice(["ibkr", "kraken", "catalog", "mock"])` option. When `data_source="kraken"`, pass through to `BacktestRequest` and ensure `DataCatalogService.fetch_or_load()` receives `data_source="kraken"`. Follow existing CLI patterns.
- [ ] T020 [US2] Add Kraken to data source selector in web UI backtest form. Update relevant Jinja2 template(s) in `src/api/templates/` to include "Kraken" option in the data source dropdown. Ensure the selected value passes through to the backtest request handler.
- [ ] T021 [US2] Run integration tests with `--forked` flag (`make test-integration`) to verify end-to-end Kraken backtest workflow. Verify T017 passes.

**Checkpoint**: User Stories 1 AND 2 both work — can fetch Kraken data AND run backtests against it via CLI and web UI.

---

## Phase 5: User Story 3 — Configure Kraken Connection Settings (Priority: P3)

**Goal**: Secure credential management with clear validation errors and multi-source coexistence.

**Independent Test**: Set Kraken env vars → system validates → fetch data succeeds. Remove env vars → clear error message. Both IBKR and Kraken configured → switch per backtest.

**Note**: Core KrakenSettings implementation is in Phase 2 (foundational). This phase covers validation edge cases and security hardening.

### Tests for User Story 3

- [ ] T022 [P] [US3] Write failing tests for credential validation edge cases in `tests/unit/services/test_kraken_settings.py`. Test: (1) `connect()` with empty credentials raises `KrakenConnectionError` with message containing "KRAKEN_API_KEY", (2) `connect()` with key but no secret raises descriptive error, (3) valid credentials succeed, (4) credentials never appear in `str()` or `repr()` of settings object. Reference spec acceptance scenarios for US3.
- [ ] T023 [US3] Enhance credential validation in `KrakenHistoricalClient.connect()` in `src/services/kraken_client.py`. Before API call: check both `api_key` and `api_secret` are non-empty, raise `KrakenConnectionError` with message guiding user to set `KRAKEN_API_KEY` and `KRAKEN_API_SECRET` env vars. On API auth failure: wrap in `KrakenConnectionError` with clear message. Make T022 tests pass.
- [ ] T024 [US3] Write security test verifying Kraken credentials never appear in log output or error messages in `tests/unit/services/test_kraken_settings.py`. Test: (1) `KrakenSettings.__repr__()` masks secret, (2) `KrakenConnectionError` messages don't contain raw credentials, (3) structured log entries from client operations don't leak secrets. Make tests pass by adding `repr=False` to secret field if needed.

**Checkpoint**: All 3 user stories complete — secure credential management, clear error messages, multi-source switching verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality assurance across all stories

- [ ] T025 [P] Run full test suite (`make test-all`) and fix any failures across all tiers
- [ ] T026 [P] Run linting and type checking (`make lint && make typecheck`) — fix all violations
- [ ] T027 Validate quickstart.md commands work: verify `uv run python -c "from src.config import get_settings; ..."` succeeds, verify CLI help shows --data-source option
- [ ] T028 Update `.env.example` (if it exists) or document in README the new Kraken environment variables: `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `KRAKEN_RATE_LIMIT`, `KRAKEN_DEFAULT_MAKER_FEE`, `KRAKEN_DEFAULT_TAKER_FEE`

**Checkpoint**: All tests green, linting clean, type checking passes, documentation complete.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Phase 2 — **MVP, implement first**
- **US2 (Phase 4)**: Depends on Phase 2 + Phase 3 (needs data fetching from US1)
- **US3 (Phase 5)**: Depends on Phase 2 + Phase 3 (enhances client from US1)
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Depends only on Foundational (Phase 2). No cross-story dependencies. **This is the MVP.**
- **US2 (P2)**: Depends on US1 — needs `KrakenHistoricalClient` and `DataCatalogService` extension to fetch data for backtesting.
- **US3 (P3)**: Depends on US1 — enhances the `KrakenHistoricalClient` already built in US1 with better credential validation and security hardening.

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD)
2. Models/mappings before services
3. Services before integration points (CLI, web UI)
4. Component tests validate service integration
5. Integration tests validate end-to-end flow

### Parallel Opportunities

**Phase 2** (after T001-T002):
- T003 and T004 can run in parallel (different files)

**Phase 3 — Tests** (after Phase 2):
- T007, T008, T009, T010 can ALL run in parallel (same file, different test classes)

**Phase 3 — Implementation**:
- T011 and T012 can run in parallel after tests pass (different concerns in same file)
- T013 depends on T011 + T012 (uses pair mapping and conversion)
- T014 can parallel with T013 (separate class)

**Phase 4**:
- T018 and T019 can run in parallel (different files)

**Phase 5**:
- T022 can start while T023/T024 wait

**Phase 6**:
- T025 and T026 can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests in parallel (TDD - write failing tests first):
Task: "T007 - Pair mapping tests in tests/unit/services/test_kraken_client.py"
Task: "T008 - OHLCV conversion tests in tests/unit/services/test_kraken_client.py"
Task: "T009 - fetch_bars tests in tests/unit/services/test_kraken_client.py"
Task: "T010 - Rate limiter tests in tests/unit/services/test_kraken_client.py"

# Then launch parallel implementation:
Task: "T011 - Pair mapping in src/services/kraken_client.py"
Task: "T012 - OHLCV conversion in src/services/kraken_client.py"

# Then sequential (dependencies):
Task: "T013 - KrakenHistoricalClient in src/services/kraken_client.py"
Task: "T014 - Rate limiter in src/services/kraken_client.py"
Task: "T015 - Component test in tests/component/services/test_kraken_catalog.py"
Task: "T016 - DataCatalogService extension in src/services/data_catalog.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (install dependency)
2. Complete Phase 2: Foundational (exceptions + settings)
3. Complete Phase 3: User Story 1 (fetch + convert + cache Kraken data)
4. **STOP and VALIDATE**: Fetch BTC/USD data, verify bars in catalog
5. MVP is deliverable — historical data pipeline works

### Incremental Delivery

1. Setup + Foundational → Infrastructure ready
2. Add US1 → Test independently → **MVP: Kraken data fetching works**
3. Add US2 → Test independently → **Backtesting with Kraken data works**
4. Add US3 → Test independently → **Credential handling hardened**
5. Polish → Full quality assurance → **Feature complete**

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- TDD is mandatory (constitution) — all test tasks must FAIL before implementation
- Integration tests require `--forked` flag (Nautilus C extension isolation)
- `kraken_client.py` must stay under 500 lines (constitution file size limit)
- All Kraken API credentials via env vars only — never hardcode (FR-008)
- Use Futures Charts API for historical data, not Spot OHLC (720-entry limit) — see research.md R-001
