# Story 5.6: Read a Paper Session Beside Its Backtest in the Existing Views

Status: done

<!-- Drafted 2026-10-11 by bmad-create-story against head a550646 (clean), branch
harness/s5-6-20261011-113730-2. Two PO rulings (R1, R2) are drafted with recommendations and must
be ruled before Task 1 — they are recorded in Task 0. -->

## Story

As the operator,
I want the sealed session to appear among my backtests and compare against the one it came from,
so that I can finally see how much to discount a backtest — which is the entire point of the phase.

## Acceptance Criteria

Verbatim from `_bmad-output/planning-artifacts/prd-epic5-scope.md` (Story 5.6). Test markers use
`@pytest.mark.acceptance_criterion("5.6a" … "5.6e", <text>, SOURCE)` with
`SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"` — the 5.3–5.5 convention.

1. **(5.6a)** **Given** a sealed session **When** the existing results list is opened **Then** the
   paper record appears alongside backtests, rendering correctly with no template, route, or query
   change (FR46, AR44).
2. **(5.6b)** **Given** the sealed record **When** its detail view is opened **Then** metrics,
   trades, and the config snapshot — including `session_conditions` — all render correctly (FR46).
3. **(5.6c)** **Given** the sealed session and the backtest named in its `linked_backtest_run_id`
   **When** both are selected in the existing 2–10 run comparison view **Then** they render side by
   side with the same metric names, formatting, and units (FR46, FR15).
4. **(5.6d)** **Given** the phase's zero-UI-change criterion **When** the diff for this epic is
   reviewed **Then** `src/api/**`, `templates/**`, `src/services/backtest_query.py`, and the
   comparison views are untouched (AR44) **And** any change to them is treated as a
   success-criterion violation rather than a judgment call.
5. **(5.6e)** **Given** the trades backfilled at seal **When** existing trade queries run **Then**
   they return the session's trades through `backtest_run_id` exactly as they do for backtests
   (AR8).

## The shape of this story — read first

**This is a verification story. It is expected to change no production code.** Every AC is a
property of a read path that already exists, over rows Stories 5.3–5.5 already write. The
deliverable is proof against the test database, the AR44 guard, docs, and the disposition of the
debt routed here.

- **If a view fails to render a sealed record, do not touch the view.** The views are frozen
  (AR44); writes to `src/api/**`, `templates/**` and `src/services/backtest_query.py` are blocked by
  the harness. A rendering failure means the *record* is wrong, so the fix belongs on the write side
  (the seal), inside the existing results vocabulary. **Any such fix that changes the stored
  record's shape is a PO question: stop and ask** before writing it.
- **No new metrics table, model, column, view or route for paper results** (FR42, AR43). No
  migration. No "Paper" badge: `run_type` makes one costless later, and that is Growth work
  (architecture, "Frontend Architecture").

## What already exists — read before writing anything

| # | Fact | Where |
|---|---|---|
| F1 | **No view filters on `run_type`.** The only readers of `run_type` in `src/` are the seal's own writes. Every list, detail, dashboard and comparison query reads `backtest_runs` unfiltered, so a sealed record is in each of them already. | `grep run_type src` → `session_seal_service.py` and `db/models/backtest.py` only |
| F2 | **Web list**: `GET /backtests` (`list.html`) and `GET /backtests/fragment` (`list_fragment.html`, the HTMX table) → `BacktestQueryService.get_filtered_backtest_list_page` → `BacktestRepository.get_filtered_backtests` (filters: strategy name, instrument `ilike`, `created_at` dates, `execution_status`) → `to_list_item`. Metrics show only when `execution_status == "success"`, which the seal writes. Cells: return `+` (only when positive) then `"%.2f%%"` of `total_return*100`, Sharpe `"%.2f"`, Max DD `"%.2f%%"` of `max_drawdown*100`. | `src/api/ui/backtests.py:406-636`; `src/api/models/backtest_list.py:217-260`; `templates/backtests/list_fragment.html:89-124`; `src/db/repositories/backtest_repository.py:500-578` |
| F3 | **Web detail**: `GET /backtests/{run_id}` → `to_detail_view`. The metrics panel (`build_metrics_panel`) has three groups: Total Return, CAGR and Final Balance; Sharpe, Sortino, Max Drawdown and Volatility; Total Trades, Win Rate and Profit Factor. Formats: `percentage` `x*100 .2f%`, `decimal` `.4f`, `currency` `$,.2f`, `integer` `,`. It also renders the trading summary. The config panel's `additional_params` is the **whole** `config_snapshot`: every top-level key renders as `{{ key\|replace('_',' ')\|title }}` / `{{ value }}`, so `session_conditions` is one "Session Conditions" row holding the dict's text. `cli_command` turns every top-level key into a `--flag`. | `src/api/models/backtest_detail.py:344-588`; `templates/backtests/detail.html`; `templates/partials/config_snapshot.html` |
| F4 | **The detail page's data calls.** The HTMX trades table is `GET /backtests/{id}/trades-table`, which calls **`http://localhost:8000/api/backtests/{id}/trades` with a real `httpx.AsyncClient`** (`import httpx` inside the function). The JS calls `/api/equity-curve/{id}` (built from trades, closed rows only — the page passes `data-backtest-id`), `/api/statistics/{id}`, `/api/drawdown/{id}`, `/api/trades/{run_id}` (chart markers), and `/api/timeseries/run/{run_id}` (catalog bars; **degrades to `[]` on any failure**). The export buttons call `/api/backtests/{id}/export?format=csv\|json`. | `src/api/ui/backtests.py:804-859`; `static/js/charts-*.js`; `src/api/rest/trades.py`; `src/api/rest/timeseries.py:144-190` |
| F5 | **The 2–10 comparison view is the CLI.** There is no web compare route (`grep -i compar src/api` is empty). `ntrader backtest compare <uuid>…` (`src/cli/commands/compare.py`) calls `SyncBacktestRepository.find_by_run_ids`. It prints rows Strategy, Symbol, Date (`created_at`), Total Return (`x*100 .2f%`), Final Balance (`$,.2f`), Sharpe Ratio (`.2f`), Max Drawdown (`x*100 .2f%`), Win Rate (`x*100 .1f%`) and Total Trades, then "Best Performer by Sharpe Ratio". It enforces 2–10. `BacktestQueryService.compare_backtests` is the service twin (2–10, `find_by_run_ids`). | `src/cli/commands/compare.py`; `src/services/backtest_query.py:147-172` |
| F6 | **The CLI's own list and detail**: `ntrader backtest history` (`find_recent` / `find_by_strategy` / top performers; `_extract_parameters` reads `config_snapshot["config"]` only) and `ntrader backtest show <run_id>` (prints `json.dumps(config_snapshot, indent=2)` in a panel, plus metric tables). | `src/cli/commands/history.py`; `src/cli/commands/show.py:169-188` |
| F7 | **A sealed record's shape** (Stories 5.3–5.5): `execution_status='success'`, `run_type='paper'`, `data_source='ibkr_live'`, `strategy_name` = the registry name title-cased (`"Sma Crossover"`, the backtest path's own rule), `instrument_symbol` from the spec's bar types. `start_date` = session `created_at` (disclosed as `start_date_basis`); `end_date` = `sealed_at`; `initial_capital` as given. `config_snapshot` = `{strategy_path, config_path, version: "1.0", config, bar_type, session_conditions: {starting_capital, rth_only, price_basis: "ibkr_unadjusted_smart", market_data_tier, closed_trade_count, open_positions_at_seal, schema_version: 1, start_date_basis, last_run_flags, equity_series: {granularity, basis, closed_trades, excludes, curve: [...]}}}`. The metrics row comes from the verbatim `save_backtest_results`. The session's trade rows have their `backtest_run_id` backfilled, plus `--force` open rows with no exit. | `src/services/session_seal_service.py:272-312, 464-490, 515-596`; `src/services/session_conditions.py`; `src/core/session_equity.py:259-269` |
| F8 | **Units are the same by construction**: `total_return = (final − start) / start` (a ratio), `max_drawdown ≤ 0` (a ratio), and `win_rate` computed from the counts by the persistence service. Both sides go through `ResultsExtractor` and `save_backtest_results` (5.1–5.3). A test must still pin **expected strings computed by hand**, never compare the code with itself (CLAUDE.md: a test that cannot fail). | `src/core/results_extractor.py:333, 538-549`; `src/services/backtest_persistence.py:257-286, 360` |
| F9 | **Story 5.4's panel test** renders the real `partials/config_snapshot.html` through the UI's own `templates` over a `SimpleNamespace` run and checks that every AR7 key appears. It is in isolation, with no route, no DB and no stored row. 5.6 owns the full route over a really sealed row. | `tests/unit/api/test_config_panel_renders_session_conditions.py` |
| F10 | **Test harnesses to reuse.** First, `tests/integration/db/conftest.py` → `db_session` (per-worker schema `test_<worker>`, dropped after), `sync_db_session`, `test_db_schema`. Second, `tests/integration/api/test_trades_api.py`: `app.dependency_overrides[get_db]` plus `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")`, cleared in `finally`. Third, `tests/integration/db/test_cli_show.py`: patch the command's `get_sync_session`. Fourth, `tests/integration/db/test_session_seal.py`: `_seed` (real state machine plus `insert_trade_if_absent`), `_seal` (reader double), `HELD`/`_holdings`/`_leg` (open legs), `expire_all()` before read-backs. | named files |
| F11 | **AR44 status at story creation.** `git diff --stat 740f8e6..HEAD -- src/api templates src/services/backtest_query.py src/cli/commands/compare.py src/cli/commands/show.py src/cli/commands/history.py static` is **empty**. `740f8e6` is the last Epic 4 commit, and Epic 5's first is `40e8f1c`. The frozen surface holds 57 tracked files under `src/api` + `templates`: 33 `.py` and 24 `.html`, including `src/api/ui/templates/`. | measured |
| F12 | **The stored `equity_series.curve` is charted by nothing.** `/api/equity/{run_id}` reads a top-level `config_snapshot["equity_curve"]`, which no record has ever stored, and falls back to two points. The page does not call it: `data-backtest-id` routes the chart to the trades-based `/api/equity-curve/{id}`. | `src/api/rest/equity.py:84-99`; `static/js/charts-equity.js:21-24` |

### Debt routed here — each must be dispositioned in the Dev Agent Record

From `deferred-work.md` and `prd-epic5-scope.md` ("Debt routed to Epic 5"):

1. **Side-unaware `profit_pct`** (a profitable SHORT reads negative), reproduced on both sides for
   parity (`deferred-work.md:2624-2630`). → **Disposition, do not fix**: AR44 freezes the views, and
   fixing one side breaks comparability. Prove both sides show the same sign convention (one
   short leg each side), and keep the item open at the epic-level both-sides owner.
2. **`trades` has no `strategy_id` column** (multi-strategy attribution). → **Disposition**: the seal
   refuses a spec with ≠ 1 strategy (`run_identity`, 5.3), so a sealed record's rows are one
   strategy's, plus R1a's reconciliation-owned open rows, which `trade_id` names. Not reachable
   through these views; AR43 forbids the column without an epic-level decision. Keep it open.
3. **Repeated `time` values in `equity_series.curve`** (`deferred-work.md:4150-4154`, "Owner: Story
   5.6, or whoever first charts it"). → **Disposition**: 5.6 charts nothing new, and F12 shows the
   series is charted by nothing. Leave the owner as "whoever first charts it".
4. **(d) "CLI Command to Replicate" is nonsense for a paper record**
   (`deferred-work.md:4284-4289`). → **R1**. Pre-existing for every record (a backtest's
   `--strategy-path … --config {…}` flags are just as wrong). Disposition, do not fix.
5. **5.2's handed observation: the curve makes the "Session Conditions" row long** (~one point per
   closed trade; 5.2 Dev Agent Record, PO ruling (a) 2026-10-07: "let 5.6 judge its rendering").
   → **R1**.
6. **Reconciliation-owned open rows exist in `trades`** (`deferred-work.md:4367-4370`). → Show they
   render as a backtest's open row does (null exit) and stay out of statistics and the equity curve
   (`trade_analytics` filters `exit_timestamp IS NOT NULL`). The attribution owner is unchanged.

## PO rulings — RULED 2026-10-11: R1a · R2b

- **R1a** — the long "Session Conditions" row **is** correct rendering. Prove it with the
  Postgres-backed test: status 200, every stored value present and escaped, a 50+ trade case
  pinned. Record the row's length and the meaningless `--session-conditions` flag in "CLI Command
  to Replicate" as known limits for later UI work. No code change; 5.2's stored document is **not**
  reopened.
- **R2b (not R2a)** — the SHA-256 manifest pin covers **the AC's four surfaces only**:
  `src/api/**`, `templates/**`, `src/services/backtest_query.py`, `src/cli/commands/compare.py`.
  `static/js` and the CLI `show`/`history` commands are left out: they are outside AR44's named
  zero-change list, and a pin there would block legitimate CLI work. Only a recorded PO decision
  may regenerate the manifest.
- **Standing instructions**: verification-only — no production code, no new table, model or view,
  no change to any AR44 path. Disposition the six routed debt items in the Dev Agent Record;
  `profit_pct` and `strategy_id` are dispositions, not fixes.

The options as drafted are kept below for the record.

### R1 — Is the config panel's `session_conditions` row "rendering correctly"? (5.2's handed judgment)

A 100-trade session's record shows one "Session Conditions" row whose value is the whole document's
text, including ~100 `{'time': …, 'value': …}` points. "CLI Command to Replicate" then carries a
`--session-conditions {…}` flag. Both come from the frozen view's own design (F3). A backtest's
nested `config` dict renders the same way.

- **[a] (recommended) Yes — it renders correctly as stored; accept, prove, disclose.** Correct
  means: HTTP 200, every stored key and value present and HTML-escaped, no `None`/`nan`/
  `Infinity` text where a backtest shows a number, and the same mechanism as a backtest's nested
  `config`. The row's length and the replicate command are recorded as known presentation limits
  for the Growth "Paper" view work. No code change; Task 3 pins the long-curve case.
- **[b] No — reopen 5.2's ruling on the write side.** Stop storing `equity_series.curve` and keep
  `granularity`, `basis`, `closed_trades` and `excludes`; the series stays reproducible from the
  backfilled trades through the same function. This shortens the row and changes 5.2's stored
  document, its tests and AR35's literal "stored". The replicate command stays wrong. It needs its
  own task, which is not drafted here.

### R2 — How AC 5.6d's "a violation, not a judgment call" is enforced

- **[a] (recommended) A frozen-surface pin test over the AC's surfaces plus the read code this
  story verifies.** A unit governance test hashes every tracked-type file (SHA-256) under
  `src/api/**` (`.py`, `.html`), `templates/**` (`.html`), `static/js/*.js`, and three files:
  `src/services/backtest_query.py`, `src/cli/commands/compare.py` (the comparison view),
  `src/cli/commands/show.py` and `src/cli/commands/history.py` (the CLI's list and detail). It
  compares them with a recorded manifest: any changed, added or removed file goes red with the
  AR44 message. The manifest is regenerated only by a deliberate, recorded PO decision. It is
  retired by the first story allowed to change a view (Growth), the size-cap baseline precedent.
- **[b]** The same pin over the AC's literal four surfaces only: `src/api/**`, `templates/**`,
  `backtest_query.py`, `compare.py`.
- **[c]** No test. Record `git diff --stat 740f8e6..HEAD -- <surfaces>` (empty) in the Dev Agent
  Record and in code review.

## Tasks / Subtasks

TDD discipline still applies. These tests assert behaviour that **already exists**, so each one must
be shown able to fail: run every new test once against a deliberately broken input or copy (a
mutation, scratch copy only — **never `git stash`**, memory) and record that it went red.

- [x] **Task 0 — Record the PO rulings R1, R2** in this file and the Dev Agent Record before code.
- [x] **Task 1 — A sealed record beside its backtest, in Postgres** (the fixture every AC uses)
      — new `tests/integration/db/test_sealed_record_in_existing_views.py`, `pytestmark =
      pytest.mark.integration`, using `tests/integration/db/conftest.py`'s `db_session`.
  - [x] 1.1 **The backtest**: write it through the real
        `BacktestPersistenceService.save_backtest_results`, with `strategy_type="sma_crossover"`,
        `strategy_name="Sma Crossover"`, `data_source="catalog"`, a 2016→2024 period, capital
        `100000.00` and a `BacktestResult` with known values. Add a few backtest-shaped trade rows
        on its `backtest_run_id` (one SHORT for debt item 1; one open row with no exit). Commit.
  - [x] 1.2 **The session**: `TradingSessionRepository.create(name=…, spec=SPEC,
        linked_backtest_run_id=<backtest run_id>)`, moved `running → stopped` through
        `SessionService.transition` (5.3's `_seed` pattern). Write its closed rows through
        `insert_trade_if_absent` with the recorder's mapping: `profit_pct` by the backtest formula,
        `holding_period_seconds` set, `commission_currency="USD"`. Include two winners, two losers
        and one profitable SHORT. Use the hand-computable P&L `+1000, −400, −300, +500` on
        `100000.00` → final `100800.00`, total return `0.008`, win rate `0.5`, 4 trades.
  - [x] 1.3 **The seal**: `seal_session(db, name, starting_capital=…, read_open_positions=<double>,
        clock=…, new_run_id=uuid4)` then `commit()`. Make one variant a `force=True` seal with one
        strategy leg and one `EXTERNAL` open leg (5.5's `HELD`), so open rows are in the record.
        Read `sealed_run_id` and `linked_backtest_run_id` **back from `trading_sessions`** after
        `expire_all()`: never use the in-memory values.
  - [x] 1.4 An ASGI client helper: `app.dependency_overrides[get_db]` yields the test session;
        `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")`;
        `dependency_overrides.clear()` in `finally` (F10).
- [x] **Task 2 — The list** (AC: 5.6a)
  - [x] 2.1 `GET /backtests` and `GET /backtests/fragment` → 200. Both run ids' short forms are in
        the same table body. The paper row shows `Sma Crossover`, `AAPL`, its date range, `+0.80%`
        and its Sharpe and Max DD, formatted as the backtest row's cells are. No `None`, `nan` or
        `Infinity` text.
  - [x] 2.2 The existing filters include it: `?strategy=Sma Crossover`, `?instrument=AAPL` and
        `?status=success` each return both rows.
  - [x] 2.3 The dashboard `GET /` → 200 with the paper record in its recent activity, because the
        dashboard reads the same rows.
  - [x] 2.4 `ntrader backtest history` (CLI list, F6) lists the paper run beside the backtest. Use
        `CliRunner` with the command module's `get_sync_session` patched to a pg8000 session on
        the test schema (`sync_db_session`'s recipe).
- [x] **Task 3 — The detail view** (AC: 5.6b; R1)
  - [x] 3.1 `GET /backtests/{paper run_id}` → 200. Every metric name `build_metrics_panel` emits is
        present, **in the same order as on the backtest's own detail page** (scrape both). The
        paper values are formatted: `0.80%`, `$100,800.00`, `50.00%` win rate, and Total Trades
        `4`. The trading summary is present. Data Source reads `ibkr_live`.
  - [x] 3.2 The config panel has a "Session Conditions" row. After `html.unescape`, every AR7 key
        and its stored value is in it, along with `ibkr_unadjusted_smart`, `start_date_basis` and
        `equity_series`. Under R1a, pin the long case: a record with ≥ 50 closed trades still
        renders 200 with every curve point's value present, and the page has no unescaped `<`
        from the stored text.
  - [x] 3.3 **Trades**: `GET /backtests/{id}/trades-table` with `httpx.AsyncClient` patched to a
        factory that returns the **real** class (captured before patching) bound to
        `ASGITransport(app=app)`. The route's own `localhost:8000` call is then answered
        in-process from the test DB, and no socket opens. → 200. Every session trade's instrument,
        side and price is in the table; the open rows show `-` for exit.
  - [x] 3.4 The page's JSON calls (F4) → 200, each over the paper record: `/api/equity-curve/{id}`
        (points = closed trades, final balance `100800.00`), `/api/statistics/{id}`,
        `/api/drawdown/{id}`, `/api/trades/{run_id}`, and `/api/backtests/{id}/export?format=csv`
        and `json`. `/api/timeseries/run/{run_id}` → 200 too; patch the bar load to raise so the
        test never reads a real catalog, which pins the existing degrade path.
  - [x] 3.5 `ntrader backtest show <paper run_id>` (CLI detail, F6) prints the snapshot JSON with
        `session_conditions` and the metric tables.
- [x] **Task 4 — The comparison view** (AC: 5.6c)
  - [x] 4.1 `ntrader backtest compare <sealed_run_id> <linked_backtest_run_id>` (ids read back per
        1.3) through `CliRunner`, with `compare.get_sync_session` patched (F10). If CliRunner does
        not capture the module-level Rich `console`, patch `compare.console` with
        `Console(file=StringIO(), width=200)`. Exit 0. Both short ids head columns. The row labels
        are **exactly** F5's nine, once each, so both columns share every metric name. The paper
        column reads `0.80%`, `$100,800.00`, `50.0%` and `4`; the backtest column reads its own
        hand-computed strings in the same formats.
  - [x] 4.2 `BacktestQueryService.compare_backtests([paper, backtest])` returns both with metrics
        loaded, the service twin. The 2–10 rule is unchanged.
  - [x] 4.3 Units pin: a paper record whose stored `total_return` were a percentage (`0.8`) would
        print `80.00%`. Prove the test would catch it with a scratch mutation, recorded in the Dev
        Agent Record.
- [x] **Task 5 — Trade queries through `backtest_run_id`** (AC: 5.6e; debt 1, 6)
  - [x] 5.1 For each query keyed on `Trade.backtest_run_id` (`src/api/rest/trades.py`: markers,
        paginated list, statistics, drawdown, equity curve, export), the set of `trade_id`s / rows
        it returns for the paper run equals the session's rows in Postgres. Assert this for the
        backtest's own rows in the same shape, so the two sides are held to one assertion.
  - [x] 5.2 The open rows (forced seal, incl. `EXTERNAL`) come back like the backtest's open row:
        present in the paginated list and the export with a null exit, one entry marker and no exit
        marker, and **absent** from statistics' counts and the equity curve's points.
  - [x] 5.3 Debt 1: a profitable SHORT's `profit_pct` reads negative on **both** sides, through the
        same endpoint. Pin it as parity, and do not fix it.
- [x] **Task 6 — The frozen-surface pin** (AC: 5.6d; R2b)
  - [x] 6.1 `tests/unit/governance/test_ar44_frozen_views.py`: a `FROZEN_VIEWS` manifest
        `{repo-relative path: sha256}` over **the AC's four surfaces only** — `src/api/**` (`.py`,
        `.html`), `templates/**` (`.html`), `src/services/backtest_query.py`,
        `src/cli/commands/compare.py` (the file globs exclude `__pycache__`). The
        test fails on any changed, added or removed file, naming each one and AR44: "zero-UI-change
        success criterion — a violation, not a judgment call; reopen with the PO". Regenerate with
        `python -m tests.unit.governance.test_ar44_frozen_views --print-manifest`, the
        `test_size_caps` precedent.
  - [x] 6.2 Not-vacuous siblings: on a `tmp_path` copy of the surface, a changed byte, an added
        `.html` and a removed `.py` are each detected. The manifest is non-empty and covers each
        surface root, so a glob that matched nothing cannot pass.
  - [x] 6.3 Provenance: record in the Dev Agent Record that the manifest was generated from a tree
        whose `git diff --stat 740f8e6..HEAD -- <surfaces>` is empty.
  - [x] 6.4 CLAUDE.md: one Anti-Patterns line naming the pin, what it guards, and that
        regenerating it is a PO decision.
- [x] **Task 7 — Docs and debt**
  - [x] 7.1 README `live seal` section: after sealing, the record is in `/backtests` and
        `ntrader backtest compare <run_id> <linked_backtest_run_id>` (both ids are in the seal
        report). No new command.
  - [x] 7.2 `docs/agent/web-ui.md` and `docs/agent/persistence.md`: paper records in the existing
        views, the known presentation limits (R1), and the price chart reading FirstRate catalog
        bars (adjusted) that may not cover the session's dates, which degrades to empty by design.
  - [x] 7.3 `deferred-work.md`: mark each routed item (debt 1–6) dispositioned in place, with a
        pointer to a new "Deferred from: story-5.6" section; log anything new.
  - [x] 7.4 No P-number: reading a sealed record is database + web only (NFR33); nothing here
        touches a broker.
- [x] **Task 8 — Gates**: `uv run ruff check --no-cache .`, `uv run ruff format --check .`,
      `make typecheck`, unit + component, `tests/integration/db/` (`--forked`, **run alone**, never
      beside another worktree's integration run), and the size-cap guard. Confirm `git diff --stat
      740f8e6..HEAD` on the frozen surface is still empty.

### Review Findings

Code review 2026-10-11, three parallel layers (Blind Hunter / Edge Case Hunter / Acceptance
Auditor). The Edge Case Hunter's first run died on a rate limit and was re-run in full; no layer
failed in the end. 0 decision-needed, 17 patch, 2 defer, 10 dismissed. **PO chose 0
(batch-apply all), 2026-10-11: all 17 patches applied**, with no production code and no AR44
path changed.

- [x] [Review][Patch] P1 — The compare test's "Date" expectation reads `created_at` through
  asyncpg, which returns UTC. `compare` reads it through pg8000, in the server's `America/Toronto`
  zone (measured: `-04:00`). The test therefore goes red every evening from 20:00 to 24:00 local.
  Read the expected value through the same pg8000 session, and also assert the backtest column's
  Strategy, Symbol and Date
  [tests/integration/db/test_sealed_record_in_cli_views.py:84] (edge+blind+auditor)
- [x] [Review][Patch] P2 — R1a's checks are not isolated to the "Session Conditions" row:
  `str(conditions)` and the AR7 loop are also satisfied by "CLI Command to Replicate". The
  60-trade case has no label check and no escaping check. Extract the row's `<dd>` and assert
  presence (unescaped) and escaping (raw) inside it, in both tests
  [tests/integration/db/test_sealed_record_in_existing_views.py:196] (auditor)
- [x] [Review][Patch] P3 — The `profit_pct` parity pin only passes stored values through. Both
  sides' values come from the test's own helper, so no production formula runs. Add a
  component-tier pin that drives one real closed SHORT `Position` through both production formulas
  (`save_trades_from_positions` and `aggregate_closed_position`). Narrow the integration test's and
  the docs' wording to what each proves
  [tests/integration/db/test_sealed_record_in_existing_views.py:378] (blind+auditor)
- [x] [Review][Patch] P4 — The fixture docstring claims both records come through "real write
  paths", but the trade rows are hand-mapped. Say so
  [tests/integration/db/_sealed_record.py:3] (blind)
- [x] [Review][Patch] P5 — `asgi_client` clears **every** dependency override on exit. Restore only
  its own `get_db` entry [tests/integration/db/_sealed_record.py:306] (blind)
- [x] [Review][Patch] P6 — The trades-table test checks string presence per row, which cannot see
  swapped columns or two legs matching one row. Compare ordered cell tuples as a multiset
  [tests/integration/db/test_sealed_record_in_existing_views.py:264] (blind)
- [x] [Review][Patch] P7 — AC 5.6e gaps:
  - drawdown is checked for status only;
  - export and markers are compared by count only;
  - the paginated-list set comparison collapses duplicate rows.

  Assert the drawdown amount from hand-computed account equity, the export rows' identity, and the
  markers' prices, all as multisets, and pin the export envelope
  [tests/integration/db/test_sealed_record_in_existing_views.py:330] (auditor+blind)
- [x] [Review][Patch] P8 — The filter tests have no negative control. Add `instrument=MSFT` and
  `status=failed` [tests/integration/db/test_sealed_record_in_existing_views.py:137] (blind)
- [x] [Review][Patch] P9 — The 60-trade test asserts `len(curve) >= 50`, which tolerates
  truncation. Pin the exact count
  [tests/integration/db/test_sealed_record_in_existing_views.py:240] (blind)
- [x] [Review][Patch] P10 — `AR7_KEYS` is imported from production code. Write the seven keys out
  [tests/integration/db/test_sealed_record_in_existing_views.py:26] (blind)
- [x] [Review][Patch] P11 — `_table` keys rows by label in a dict, so a duplicated label collapses.
  Assert the label list instead [tests/integration/db/test_sealed_record_in_cli_views.py:69]
  (auditor)
- [x] [Review][Patch] P12 — When `compare` prints an error and still exits 0, `_table` raises a bare
  `IndexError` and hides the message [tests/integration/db/test_sealed_record_in_cli_views.py:72]
  (edge)
- [x] [Review][Patch] P13 — `candles == []` cannot fail: the bar load is forced to raise, and the
  route swallows the error. Assert record-dependent fields instead
  [tests/integration/db/test_sealed_record_in_existing_views.py:308] (edge)
- [x] [Review][Patch] P14 — Governance pin fixes:
  - the R2b exactness check is one-directional on `SURFACE_FILES`: pin `SURFACE_TREES` and
    `SURFACE_FILES` to the ruled literals;
  - the comment wrongly says `_IGNORED_PARTS` covers `.DS_Store`;
  - the coverage test's docstring needs clarifying;
  - document that an untracked stray file in a frozen tree is reported too.

  [tests/unit/governance/test_ar44_frozen_views.py:43] (blind+edge)
- [x] [Review][Patch] P15 — The `show` test asserts the "Total Trades" label but not its value
  [tests/integration/db/test_sealed_record_in_cli_views.py:168] (blind)
- [x] [Review][Patch] P16 — The fixture does not say that `profit_loss` is the row's realized
  P&L, net of commission, summed as is [tests/integration/db/_sealed_record.py:58] (blind)
- [x] [Review][Patch] P17 — The Dev Agent Record must state the mutation sweep's deviation: an
  in-place probe in the new, untracked fixture, not a scratch copy (spec: "scratch copy only")
  [5-6-…md, Dev Agent Record] (auditor)
- [x] [Review][Defer] W1 — The trades-table route calls itself over HTTP at a hard-coded
  `http://localhost:8000`, so it breaks when the app serves on another host or port
  [src/api/ui/backtests.py:835] — deferred, pre-existing (AR44-frozen file)
- [x] [Review][Defer] W2 — The CLI views read through pg8000 and show timestamps in the Postgres
  server's zone. The web views read through asyncpg and show UTC, so one record's date differs
  between `backtest compare` and `/backtests` late in the evening
  [src/cli/commands/compare.py:194] — deferred, pre-existing (every record)

Dismissed (10):
- The `SET search_path` pattern: it mirrors conftest's `sync_db_session`, the commands are
  read-only, and a lost search path fails loudly.
- Manifest dotfiles, symlinked directories, CRLF, provenance and regeneration enforcement: process
  rules. There are no symlinks and no Windows checkouts, and the auditor verified provenance.
- `in_process` is a function, not a class: nothing calls `isinstance` on it.
- The indicators endpoint is not called: the detail page never calls it.
- The −6.88% headline drawdown against the −0.69% trades chart: 5.2 ruling R1b, the same on both
  sides.
- The Sharpe value and list dates are read from the DB: disclosed, and both sides read through
  asyncpg.
- Wall-clock `created_at` and future exit times: 5.3's clock-injection pattern.
- The dashboard and history checks are presence-only: presence is the claim.
- The HTML and Rich parsing is brittle: it fails loudly, and the markup is frozen by
  `FROZEN_VIEWS`.
- The 5.6d pin is stricter than `.py`/`.html`: not a violation.

## Dev Notes

### Design decisions

- **D-A — No production change.** See "The shape of this story". If one proves necessary, it is
  on the seal's write side, inside the existing vocabulary, and a shape change goes to the PO first.
- **D-B — The proof tier is integration (Postgres)**, through the real write path (5.3's
  `seal_session`, the backtest's `save_backtest_results`) and the real read path (the FastAPI app
  in-process; the real Click commands). Mocks and `SimpleNamespace` runs prove nothing about the
  stored record. 5.4's panel test (F9) already covers the template in isolation.
- **D-C — "Same names, formatting and units" is pinned by hand-computed expected strings** and by
  the comparison's row labels being one shared list (F5). `0.80%`, not `round(x*100, 2)`.
- **D-D — The linked backtest is read from the session row** (`trading_sessions.
  linked_backtest_run_id`), the way the operator gets it from `live create --compare-to` and the
  seal report. It is not taken from the test's own variable.
- **D-E — "Existing trade queries" = every query keyed on `Trade.backtest_run_id`** (seven
  statements across the six endpoints of `src/api/rest/trades.py`), plus the HTMX table that
  renders one of them.
- **D-F — "Renders correctly"** = HTTP 200; the expected labels present; values in the view's own
  formats; stored text escaped; no `None`/`nan`/`Infinity` where a backtest shows a number. A
  metric that is genuinely absent shows `N/A`, as it does for a backtest.

### Scope boundaries — do NOT build

- Nothing under `src/api/**`, `templates/**`, `static/**`, `src/services/backtest_query.py`,
  `src/cli/commands/{compare,show,history}.py`, `BacktestOrchestrator`, or the import/catalog
  commands (AR44).
- No "Paper" badge, `run_type` column, filter or route; no compare-in-browser; no change to the seal
  report's fields (it already names `run_id` and `linked_backtest_run_id`).
- No migration; no change to `session_conditions` keys (except under R1b, which needs its own task);
  no metric-math change (FR41); no fix to `profit_pct` (debt 1) or the replicate command (debt 4).
- Never run `live seal` or `live start` against a real session; no browser run against the
  operator's database. The server-rendered HTML and every JSON endpoint the page's JS calls are
  proven in-process, and the JS itself is frozen code.

### Hazards

- **The trades-table route makes a real HTTP call** to `localhost:8000` (F4). Patch
  `httpx.AsyncClient` to an ASGI-bound client. Capture the real class first, or the factory recurses.
  A failing patch must never fall through to the network.
- **`app.dependency_overrides` is process-global**: clear it in `finally`; `--forked` isolates
  tests, but not a single test's later requests.
- **Identity map** (5.3): `expire_all()` before every read-back, and before the routes run if the
  test session wrote the rows.
- **One schema per worker** (`test_<worker>`): the async `db_session` and a pg8000 sync session in
  the same test must both `SET search_path` to it. Commit through the async session before the CLI
  reads.
- **Rich in `CliRunner`**: modules keep a module-level `Console()`. Assert substrings, and set a wide
  console if columns truncate (`compare.py` fixes column widths at 20/15).
- **The list sorts by `created_at`**: the sealed row is created at seal time (server default), not
  at the injected clock. Do not assert an order that depends on it.
- **`trades_table.html` renders `None|float` as `0`**: a null `profit_pct` prints `0.00%`. Seed
  `profit_pct` like the recorder does (1.2), or the table cannot tell a real value from a missing
  one.
- **A test that cannot fail**: every pin needs a not-vacuous sibling or a recorded mutation; every
  exact-set pin gets checked both ways (CLAUDE.md).
- **Shared Postgres**: a forked integration run in another worktree collides on schema creation
  (5.4/5.5 Dev Agent Records). Run `tests/integration/db` alone; if `pg_namespace` errors appear,
  re-run alone before diagnosing.
- **ruff's warm cache hides I001**: use `--no-cache` (memory).

### Project Structure Notes

- New: `tests/integration/db/test_sealed_record_in_existing_views.py`,
  `tests/unit/governance/test_ar44_frozen_views.py` (R2b).
- Edit: `README.md`, `docs/agent/web-ui.md`, `docs/agent/persistence.md`, `CLAUDE.md` (one
  Anti-Patterns line), `_bmad-output/implementation-artifacts/deferred-work.md`,
  `sprint-status.yaml` (the 5-6 key only), this file.
- No `live_*` module is touched, so no guard list needs re-checking; no new stdlib import enters a
  live-path module.
- Size caps: each new test file < 500 statements, each function < 50 — split helpers early.

### Previous-story intelligence (5.3–5.5)

- 5.5 split `src/services/seal_open_positions.py` out of `session_seal_service.py`, because the two
  stories together hit 511/500 statements. Neither file should grow here.
- Read-backs are raw SQL after `expire_all()`; seeds are committed so a rollback cannot erase them.
- Open rows: `client_order_id=None`, `exit_price=None`, `exit_timestamp=None`, `profit_pct=None`,
  `holding_period_seconds=None`; `trade_id` is the Nautilus position id (`…-EXTERNAL` for the
  reconciliation owner).
- Disposition wording bans the AR36 stems (`close`/`kill`/`halt`/`pause`/`finalize`); not relevant
  unless operator-facing text is added (it should not be).
- Every Epic 5 review found at least one test that could not fail. Record a mutation per AC.

### References

- [Source: _bmad-output/planning-artifacts/prd-epic5-scope.md — Story 5.6, AR5, AR7, AR8, AR43, AR44, FR46, "Debt routed to Epic 5"]
- [Source: _bmad-output/planning-artifacts/architecture.md#Frontend Architecture, #Data Architecture D1]
- [Source: _bmad-output/implementation-artifacts/5-2-reconstruct-a-sealed-sessions-equity-curve-from-its-trades.md — "Observation handed to Story 5.6"]
- [Source: _bmad-output/implementation-artifacts/5-4-record-the-conditions-the-session-actually-ran-under.md — F12]
- [Source: _bmad-output/implementation-artifacts/5-5-handle-open-positions-at-seal-time-explicitly.md — open-row shape, Hazards]
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:2624-2630, 4150-4154, 4284-4289, 4367-4370]
- [Source: src/api/ui/backtests.py, src/api/models/backtest_detail.py, src/api/models/backtest_list.py, src/api/rest/trades.py, src/cli/commands/compare.py — read only]
- [Source: tests/integration/api/test_trades_api.py, tests/integration/db/test_session_seal.py, tests/integration/db/test_cli_show.py — harness patterns]
- [Source: CLAUDE.md — membership pins, size caps, "a test that cannot fail"]

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`)

### Debug Log References

- First run of the web-view tests: 2 of 13 failed, both on my own expectations, not on the record.
  `GET /backtests` answers `307` → `/backtests/` (the route's canonical form; the nav link follows
  the same redirect). The helper's client now follows redirects. The trades-based equity curve
  starts with a starting-capital point (`trade_number` 0), so it has closed + 1 points, on both
  sides alike.
- The governance pin was first seen red with an empty manifest (8 failed), then generated.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created
  (bmad-create-story, 2026-10-11).

**Task 0 — PO rulings (2026-10-11): R1a · R2b.**
- **R1a** — the long "Session Conditions" row is correct rendering. It is proven against Postgres:
  status 200, every stored value present and HTML-escaped, a 60-trade case pinned. The row's
  length and the meaningless `--session-conditions` replicate flag are recorded as known limits
  for later UI work. 5.2's stored document is not reopened.
- **R2b** — the frozen-surface manifest covers the AC's four surfaces only: `src/api/**`,
  `templates/**`, `backtest_query.py`, `compare.py`. `static/js` and the CLI `show`/`history`
  stay out. Only a recorded PO decision may regenerate it.

**What shipped (verification only — zero production code, zero AR44 change).**
- `tests/integration/db/_sealed_record.py` builds the pair. A backtest is written through the real
  `save_backtest_results`, with its own rows: a profitable SHORT and one open row. A session
  linked to it (`--compare-to`'s column) moves `running → stopped` through `transition()`. Its
  closed rows use the recorder's mapping: two winners (one a profitable SHORT) and two losers,
  P&L +800 on 100,000. It is then **force-sealed** through `seal_session` over a strategy leg and
  an `EXTERNAL` leg. The ids are read back from `trading_sessions` (`sealed_run_id`,
  `linked_backtest_run_id`). The helper also provides the in-process ASGI client (`get_db`
  overridden) and a pg8000 session on the test schema for the Click commands.
- `tests/integration/db/test_sealed_record_in_existing_views.py` (13 tests):
  - **5.6a**: the list and its fragment show both rows cell by cell, against hand-computed
    strings (`+0.80%`, `-6.88%`; `+25.00%`, `1.85`, `-12.00%`). The strategy, instrument and
    status filters keep the paper row, and the dashboard lists it.
  - **5.6b**: the metric names are the ten `build_metrics_panel` names, in the same order on both
    detail pages. The values are hand-computed: `0.80%`, `$100,800.00`, `-6.88%`, `4`, `50.00%`
    and profit factor `2.1429` (1500/700). The config panel holds every stored condition and
    escapes it (R1a), and the 60-trade record renders every curve point. The HTMX trades table is
    answered in-process: its own `localhost:8000` call is patched onto `ASGITransport`, and it
    lists all six rows, the open ones with `-` for exit. Nine page calls answer 200 for the paper
    record, the price chart degrading to no candles.
  - **5.6e**: every trade query keyed on `backtest_run_id` returns the session's rows, held to the
    same assertions as the backtest's: the paginated list, export, markers (one per row plus one
    per closed row), statistics and the equity curve. Open rows come back like a backtest's. A
    profitable SHORT's `profit_pct` reads negative on both sides (debt 1, pinned as parity).
- `tests/integration/db/test_sealed_record_in_cli_views.py` (4 tests):
  - **5.6c**: `ntrader backtest compare <sealed_run_id> <linked_backtest_run_id>` runs unmodified
    through Click. The rows are exactly compare.py's nine labels, shared by both columns. The
    paper column reads `0.80%`, `$100,800.00`, `-6.88%`, `50.0%` and `4`. The backtest column
    reads `25.00%`, `$125,000.00`, `1.85`, `-12.00%`, `66.7%` and `3`.
  - The `compare_backtests` service twin returns both runs. `ntrader history` lists both, and
    `ntrader backtest show` prints `session_conditions`.
- `tests/unit/governance/test_ar44_frozen_views.py` (**5.6d**, R2b): `FROZEN_VIEWS` pins 59
  files, which equal `git ls-files` over the four surfaces. Any changed, added or removed file goes
  red with the AR44 message. Five not-vacuous siblings run on a `tmp_path` copy: an untouched
  copy matches; a changed byte, an added file and a removed file are each named; `.DS_Store` and
  `__pycache__` litter are ignored. Coverage and R2b-exactness pins sit beside them.
  `--print-manifest` prints the manifest verbatim as written (checked).
- **Provenance (6.3)**: `git diff --stat 740f8e6..HEAD` over the four surfaces was empty, the
  working tree on them clean, when the manifest was generated. After all work,
  `git diff --stat 740f8e6 -- <surfaces>` (working tree included) is still empty, and `git status
  -- src static` shows no change.
- Docs: README (a `backtest compare` row, plus a "Reading a sealed session beside its backtest"
  paragraph), `docs/agent/web-ui.md` (a new "Sealed Paper Sessions in These Views" section),
  `docs/agent/persistence.md` (the read side) and CLAUDE.md (a `FROZEN_VIEWS` membership-pin
  entry).
- **No P-number**: reading a sealed record is database + web only (NFR33). `live seal` and
  `live start` were never run against a real session.

**Corrections to the drafted Dev Notes.**
- F11 said the surface includes `src/api/ui/templates/`. It has no `.html` under `src/api` at all:
  all 24 templates are in `templates/`. The coverage pin asserts the real layout.
- Task 3.2's "no unescaped `<` from the stored text" could not fail, because the stored document
  holds no `<`. Escaping is proven by the quotes instead: the dict's text is on the page only as
  entities (`'` → `&#39;`). That is the same Jinja autoescape, on text the record really holds.
- Task 3.4 also covers `/backtests/{run_id}/export` and `/api/backtests/{id}/trades`; the equity
  curve has closed + 1 points (see Debug Log).

**Debt dispositions** (deferred-work, "Deferred from: story-5.6"; items 1–4 and 6 also marked in
place):
1. Side-unaware `profit_pct` — **disposition, not fix**: pinned as parity on both sides.
2. No `strategy_id` on `trades` — **disposition, not fix**: unreachable for a sealed record,
   because `run_identity` refuses a spec with more than one strategy.
3. Repeated curve `time` values — still nothing charts the stored series; the owner stays "whoever
   first charts it".
4. "CLI Command to Replicate" — R1a: a known presentation limit.
5. 5.2's long-row observation — R1a: correct rendering, proven, disclosed.
6. Reconciliation-owned open rows — checked: they render as a backtest's open row does.

New items logged: the price chart reads FirstRate catalog bars for a paper record; nothing marks a
paper row in the list; `FROZEN_VIEWS` will block the first legitimate view change, by design.

**Mutation sweep: 6/6 killed.** A temporary probe in the fixture broke the stored record after the
seal; it has since been removed, and `grep MUTATION tests` is empty. The views were never touched.
- M1, `total_return × 100`: killed by the list, detail and compare tests (Task 4.3's units pin).
- M2, `execution_status='failed'`: killed by the list, the `status=success` filter, detail and
  `show`.
- M3, `session_conditions` removed: killed by the config panel test and `show`.
- M4, one trade unlinked from the run: killed by the trades table, the page calls and the trade
  queries.
- M5, the short's `profit_pct` made positive: killed by the parity test.
- M6, the `EXTERNAL` open row deleted: killed by the trades table, the trade queries and the open
  rows test.
- The governance pin was seen red with an empty manifest, and its four mutation siblings pass.

**Gates (2026-10-11).**
- `ruff check --no-cache .` all checks passed; `ruff format --check .` 585 files formatted;
  `make typecheck` no issues (124 files).
- Unit: 3858 passed, size-cap guard included.
- Component: 2000 passed, 16 skipped.
- `tests/integration/db` + `tests/integration/api` (`-n auto --forked`, run alone): 193 passed.

**Code review (2026-10-11).** Three layers ran: Blind Hunter, Edge Case Hunter (re-run after a
rate-limit failure) and Acceptance Auditor. 17 patch, 2 defer and 10 dismissed. The PO chose 0,
batch-apply all. All 17 are applied, with no production code and no AR44 path changed:
- **P1 (High).** pg8000 returns `timestamptz` in the server's zone (`America/Toronto`, measured
  `-04:00`), where asyncpg returns UTC.
  - The compare test now reads its expected dates the way `compare` does: pg8000, in the same
    session zone.
  - It is parametrized over UTC, UTC+14 and UTC−11, so at any hour one zone is on another date.
  - Seen red first: the old expectation failed under `Pacific/Kiritimati` (`2026-10-12` against
    the 11th).
  - The backtest column's Strategy, Symbol and Date are now asserted too.
  - `sync_session_on` gained a `time_zone` parameter.
- **P2.** `_conditions_cell` extracts the one "Session Conditions" `<dd>`, and
  `_assert_rendered_escaped` holds it to `unescape(cell) == str(conditions)` with every quote an
  entity. Both the 4-trade and 60-trade tests use them. A pure not-vacuous sibling proves the same
  text in "CLI Command to Replicate" beside an empty row does not pass.
- **P3.** New `tests/component/services/test_profit_pct_parity.py` runs one real closed Nautilus
  `Position` through `save_trades_from_positions` and `aggregate_closed_position`: a profitable
  SHORT gives −5 on both, a profitable long +5 on both. The integration test's docstring,
  deferred-work and `persistence.md` now say what each test proves.
- **P4, P16.** The fixture docstring says the trade rows are hand-mapped, and that `profit_loss`
  is net realized P&L summed as is; the account path for the drawdown is written out.
- **P5.** `asgi_client` saves and restores only its own `get_db` override.
- **P6.** The trades table is compared as a multiset of cell tuples: instrument, side, quantity,
  entry, exit, P&L, P&L % and holding period, hand-computed from the legs.
- **P7.** Every trade query is compared row by row as a multiset:
  - the paginated keys and the JSON export's identity, with its bare-list envelope and exact keys;
  - the markers as `(side, price, pnl)`;
  - the trades-based drawdown amount, hand-computed: 700 for the paper record (peak 101,000,
    trough 100,300), 200 for the backtest.
- **P8.** Negative controls: `instrument=MSFT` and `status=failed` drop both rows.
- **P9.** The 60-trade curve is pinned at exactly 60 points, one per distinct close day.
- **P10.** The seven AR7 keys and the export keys are written out, not imported.
- **P11, P12.** `_table` returns labels as a list, so a duplicate is seen, and fails with the
  printed output when `compare` printed no table.
- **P13.** The timeseries check asserts what the record decides: symbol `AAPL` and bar type
  `1-MINUTE-LAST`. The forced-empty candles are described as the route's own degrade path.
- **P14.** The R2b exactness test pins `SURFACE_TREES` and `SURFACE_FILES` to the ruling's own
  literals, so widening the constants now goes red. The comments and docstrings now explain:
  - that dot-paths are ignored;
  - the coverage test's purpose, which is to catch a regeneration from an empty glob;
  - that an untracked stray file in a frozen tree is reported too.
- **P15.** `show`'s "Total Trades" row is pinned with its value, 4.
- **P17 — disclosed deviation.** The dev-story mutation sweep (M1–M6) did **not** use a scratch
  copy as the spec's Tasks preamble required. A temporary probe was edited into the new, then
  untracked, `_sealed_record.py` and removed afterwards (`grep MUTATION tests` empty). Its 6/6
  result cannot be re-verified from the tree.

**Review-patch mutation sweep: 5/5 killed, by the scratch-copy method this time.** Each target
was copied to `<file>.s56orig`, mutated, run, and restored with `mv`. The production file was
then checked byte-identical with `git diff --quiet`, and no `*.s56orig` is left.
- M9: the recorder's formula made side-aware on one side only. The component SHORT pin went red
  and its long control stayed green.
- M10: one session row's entry and exit swapped. Killed by the cell-exact trades table, which the
  old presence checks could not see.
- M11: one closed row's P&L lowered by 100. Killed by three tests: the trades table, the
  page-calls test (final balance 100,700) and the trade queries (drawdown 800 against 700).
- M12: the stored bar type changed. Killed by the timeseries assertion.
- M13: one curve point dropped. Killed: 59 against 60, which the old `>= 50` let through.
- P1 was also seen red before its fix, as recorded above.

**Gates after the review (2026-10-11).**
- `ruff check --no-cache .` all checks passed; `ruff format --check .` 586 files formatted;
  `make typecheck` no issues (124 files).
- Unit: 3858 passed. Component: 2002 passed, 16 skipped.
- `tests/integration/db` + `tests/integration/api` (`-n auto --forked`, run alone): 198 passed;
  5.6a, 5.6b, 5.6c and 5.6e PASS. 5.6d PASS in the unit run.
- `git status --porcelain -- src templates static` is empty, and `git diff --stat 740f8e6` over
  the four frozen surfaces is empty.

### File List

- `tests/integration/db/_sealed_record.py` (new) — the sealed-beside-backtest fixture and read-side
  helpers
- `tests/integration/db/test_sealed_record_in_existing_views.py` (new) — 5.6a, 5.6b, 5.6e
- `tests/integration/db/test_sealed_record_in_cli_views.py` (new) — 5.6c, CLI history/show
- `tests/unit/governance/test_ar44_frozen_views.py` (new) — 5.6d, `FROZEN_VIEWS`
- `tests/component/services/test_profit_pct_parity.py` (new, code review P3) — both production
  `profit_pct` formulas on one real SHORT
- `README.md` — `backtest compare` row; reading a sealed session beside its backtest
- `docs/agent/web-ui.md` — "Sealed Paper Sessions in These Views"
- `docs/agent/persistence.md` — the read side of a sealed record
- `CLAUDE.md` — `FROZEN_VIEWS` membership-pin entry
- `_bmad-output/implementation-artifacts/deferred-work.md` — story-5.6 section; routed items marked
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — the 5-6 key only
- `_bmad-output/implementation-artifacts/5-6-read-a-paper-session-beside-its-backtest-in-the-existing-views.md`

### Change Log

- 2026-10-11 — Story drafted (bmad-create-story); R1/R2 drafted for the PO.
- 2026-10-11 — PO ruled R1a · R2b (manifest over the AC's four surfaces only).
- 2026-10-11 — Implemented (bmad-dev-story). This is verification only: a record force-sealed beside
  its linked backtest is proven in Postgres across the web list, detail, trades and page calls and
  the CLI compare, history and show. AR44 is pinned by `FROZEN_VIEWS`. The six debt items are
  dispositioned, and a 6/6 mutation sweep killed every mutation. Zero production code, zero AR44
  change. Status → review.
- 2026-10-11 — Code review (bmad-code-review): 17 patch, 2 defer (W1, W2 in deferred-work) and 10
  dismissed. The PO chose batch-apply (0), and all 17 patches are applied. They include P1, a
  High time-of-day failure in the compare test, and a new component pin for both `profit_pct`
  writers (P3). A 5/5 review-patch mutation sweep used scratch copies. Zero production code, zero
  AR44 change. Status → done.
