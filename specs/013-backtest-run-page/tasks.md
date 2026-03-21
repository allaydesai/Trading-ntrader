# Tasks: Backtest Run Page

**Input**: Design documents from `/specs/013-backtest-run-page/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Included per constitution (TDD is non-negotiable). Tests written first, must fail before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create new files and shared models needed by all user stories

- [ ] T001 [P] Create BacktestRunFormData and StrategyOption Pydantic models in src/api/models/run_backtest.py — include all fields from data-model.md (strategy, symbol, start_date, end_date, data_source, timeframe, starting_balance, timeout_seconds, strategy_params) with validation constraints
- [ ] T002 [P] Add "Run Backtest" link to navigation in templates/partials/nav.html — add link to `/backtests/run` in both desktop nav and mobile menu sections, with conditional active-page styling for `nav_state.active_page == 'run_backtest'`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Unit tests for shared models — must FAIL before Phase 3 implementation begins

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Write unit tests for BacktestRunFormData validation in tests/unit/api/test_run_backtest_models.py — test cases: valid form data, missing required fields (strategy, symbol, start_date, end_date), start_date after end_date rejected, invalid data_source rejected, invalid timeframe rejected, starting_balance <= 0 rejected, timeout_seconds <= 0 rejected, default values applied correctly

**Checkpoint**: Foundation ready — run `make test-unit` and confirm T003 tests FAIL (models exist but no routes yet)

---

## Phase 3: User Story 1 — Configure and Launch a Backtest (Priority: P1) 🎯 MVP

**Goal**: Users can fill out a form with strategy, symbol, dates, data source, timeframe, balance and submit to run a backtest. On success, they are redirected to the results detail page. On failure, they see an error message.

**Independent Test**: Navigate to `/backtests/run`, fill in form with valid strategy/symbol/dates, submit, and verify redirect to `/backtests/{run_id}`

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T004 [P] [US1] Write component test for GET /backtests/run in tests/component/api/test_run_backtest_routes.py — test that route returns 200, template contains strategy dropdown populated with all registered strategies, form fields for symbol/dates/data_source/timeframe/balance/timeout are present
- [ ] T005 [P] [US1] Write component test for POST /backtests/run in tests/component/api/test_run_backtest_routes.py — test cases: successful submission redirects to detail page (mock BacktestOrchestrator), validation error re-renders form with errors, execution error shows error message in response body

### Implementation for User Story 1

- [ ] T006 [US1] Implement GET /backtests/run route handler in src/api/ui/backtests.py — query StrategyRegistry.list_strategies() for dropdown options, build template context with strategies, data_sources list, timeframe list, defaults (from Settings), and NavigationState with active_page="run_backtest". Return templates/backtests/run.html
- [ ] T007 [US1] Create form template templates/backtests/run.html — extend base.html, include nav partial, render form with: strategy dropdown (name + description), symbol text input with placeholder "AAPL or AAPL.NASDAQ", start_date and end_date date inputs, data_source dropdown (catalog/ibkr/kraken/mock), timeframe dropdown, starting_balance number input (default 1000000), timeout_seconds number input (default 300), submit button. Include error display areas for validation errors and execution errors. Use Tailwind styling consistent with existing pages
- [ ] T008 [US1] Implement POST /backtests/run route handler in src/api/ui/backtests.py — parse form data into BacktestRunFormData, validate inputs (return re-rendered form with errors on failure), call resolve_backtest_request() to create BacktestRequest, call load_backtest_data() to load bars, create BacktestOrchestrator and execute with asyncio.wait_for(timeout), on success return HX-Redirect header to /backtests/{run_id}, on execution error return error HTML fragment, on timeout return timeout-specific error message. Always dispose orchestrator in finally block

**Checkpoint**: At this point, User Story 1 should be fully functional — run `make test-component` to verify T004/T005 pass, then manually test at http://127.0.0.1:8000/backtests/run

---

## Phase 4: User Story 2 — Configure Strategy-Specific Parameters (Priority: P2)

**Goal**: When a strategy is selected, the form dynamically loads that strategy's configurable parameters (e.g., fast_period, slow_period) with defaults pre-filled. Users can adjust parameters before running.

**Independent Test**: Select different strategies in the dropdown and verify parameter fields change dynamically with correct defaults

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T009 [P] [US2] Write unit test for StrategyParamField model and schema-to-fields helper in tests/unit/api/test_run_backtest_models.py — test that param_model.model_json_schema() is correctly parsed into StrategyParamField list with name, field_type, default, min, max, required for SMAParameters and MomentumParameters
- [ ] T010 [P] [US2] Write component test for GET /backtests/run/strategy-params/{strategy_name} in tests/component/api/test_run_backtest_routes.py — test cases: valid strategy returns HTML fragment with parameter inputs, unknown strategy returns empty fragment, parameter inputs have correct default values and name attributes prefixed with "param_"

### Implementation for User Story 2

- [ ] T011 [US2] Add StrategyParamField model and schema-to-fields helper function in src/api/models/run_backtest.py — parse param_model.model_json_schema() properties into list of StrategyParamField instances, handle integer/number/string/boolean types, extract min/max/default/required from JSON schema
- [ ] T012 [US2] Implement GET /backtests/run/strategy-params/{strategy_name} route handler in src/api/ui/backtests.py — look up strategy via StrategyRegistry.get(), extract param_model, convert to StrategyParamField list via helper, return rendered templates/backtests/partials/strategy_params.html fragment. Return empty div if strategy not found
- [ ] T013 [US2] Create strategy params HTMX fragment template templates/backtests/partials/strategy_params.html — render labeled input fields for each StrategyParamField: number inputs for integer/number types (with min/max attributes), text inputs for string, checkboxes for boolean. Each input named "param_{field.name}" with default value pre-filled. Use Tailwind styling consistent with form
- [ ] T014 [US2] Update templates/backtests/run.html to add HTMX dynamic loading — add hx-get="/backtests/run/strategy-params/{value}" on strategy dropdown change (hx-trigger="change"), target a #strategy-params div, add hx-swap="innerHTML". Update POST handler in T008 to extract param_ prefixed fields from form data and pass as strategy_params dict

**Checkpoint**: User Stories 1 AND 2 should both work — selecting a strategy dynamically loads its parameters, and submitting with custom parameters runs the backtest correctly

---

## Phase 5: User Story 3 — View Backtest Progress (Priority: P3)

**Goal**: After submitting, users see a spinner/progress indicator while the backtest runs. Concurrent submissions are blocked with a clear message.

**Independent Test**: Submit a backtest and verify spinner appears, then disappears when results load or error shows

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T015 [P] [US3] Write component test for concurrent execution prevention in tests/component/api/test_run_backtest_routes.py — test that submitting while a backtest is running returns 409 Conflict with "backtest already in progress" message (use asyncio.Lock mock or slow mock orchestrator)
- [ ] T016 [P] [US3] Write component test for timeout handling in tests/component/api/test_run_backtest_routes.py — test that a backtest exceeding timeout_seconds returns a timeout error message suggesting shorter date range or larger timeframe

### Implementation for User Story 3

- [ ] T017 [US3] Add asyncio.Lock for concurrent execution prevention in src/api/ui/backtests.py — add module-level `_backtest_lock = asyncio.Lock()` and update POST handler to use `_backtest_lock.locked()` check before execution. If locked, return 409 with "backtest already in progress" HTML. Acquire lock around execution block, release in finally
- [ ] T018 [US3] Add HTMX progress indicator to templates/backtests/run.html — add hx-indicator attribute to the form/submit button pointing to a spinner element. Add a hidden spinner div (CSS animated) that HTMX shows during the POST request. Disable submit button via hx-disabled-elt during submission. Style spinner with Tailwind (animate-spin)

**Checkpoint**: All user stories should now be independently functional — progress spinner shows during execution, concurrent submissions blocked

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Edge case handling, cleanup, and final validation

- [ ] T019 Verify form re-population on validation error — ensure that when POST validation fails, the form is re-rendered with previously submitted values preserved in all fields (form_data context variable)
- [ ] T020 Run full test suite and fix any failures — execute `make test-unit && make test-component` and verify all new tests pass. Check that existing tests are not broken
- [ ] T021 Run quickstart.md validation — follow specs/013-backtest-run-page/quickstart.md steps end-to-end to verify the feature works as documented

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on T001 (models must exist for tests to import) — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Phase 2 completion
- **User Story 2 (Phase 4)**: Depends on Phase 3 (US1) — extends the form and POST handler
- **User Story 3 (Phase 5)**: Depends on Phase 3 (US1) — modifies the POST handler and template
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependencies on other stories
- **User Story 2 (P2)**: Depends on US1 — extends the form template and POST handler
- **User Story 3 (P3)**: Depends on US1 — modifies the POST handler and form template. Can be done in parallel with US2 if changes are coordinated

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models/helpers before route handlers
- Route handlers before templates
- Templates complete the story

### Parallel Opportunities

- T001 and T002 can run in parallel (different files)
- T004 and T005 can run in parallel (same file but independent test functions)
- T009 and T010 can run in parallel (different test files)
- T015 and T016 can run in parallel (same file but independent test functions)

---

## Parallel Example: User Story 1

```bash
# Launch tests in parallel (different test concerns):
Task T004: "Component test for GET /backtests/run"
Task T005: "Component test for POST /backtests/run"

# Then implement sequentially:
Task T006: "GET route handler" (needed by T007)
Task T007: "Form template" (needed by T008)
Task T008: "POST route handler" (completes the story)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003)
3. Complete Phase 3: User Story 1 (T004-T008)
4. **STOP and VALIDATE**: Test US1 independently — form renders, backtest executes, redirects to results
5. Deploy/demo if ready — users can already run backtests from the browser

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo (strategy params)
4. Add User Story 3 → Test independently → Deploy/Demo (progress + concurrency)
5. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Constitution mandates TDD — all test tasks must be completed before their corresponding implementation tasks
- BacktestEngine is single-use — orchestrator.dispose() must always be called in finally block
- Nautilus LogGuard — never instantiate LiveLogger directly in route handlers
- Integration tests need --forked — use `make test-integration` for any tests that touch the real engine
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
