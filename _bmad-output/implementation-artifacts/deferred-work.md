# Deferred Work

## Deferred from: code review of 1-1-catalog-foundation-and-configuration (2026-04-06)

- W1: Async upsert TOCTOU race — SELECT then UPDATE without `FOR UPDATE` lock in `CatalogInstrumentRepository.upsert()`. Unique constraint catches collisions; acceptable for sequential import pipeline. Revisit if concurrent web API upserts are added.
- W2: `CatalogConfig.name` field redundant with dict key in `CatalogSettings.catalogs` — no validation they match. Low risk but could cause confusion.
- W3: `CatalogManager` is general-purpose catalog resolver but lives under `src/services/firstrate/`. Can relocate to `src/services/` if additional non-FirstRate catalogs are added.

## Deferred from: code review of 1-2-etf-csv-parser-with-ohlc-validation (2026-04-07)

- F8: `_row_to_bar` silently swallows all exceptions — dropped rows are invisible to callers. Design-level decision for Story 1.4 import pipeline (may need a parse result object with skipped-row counts).
- F9: No duplicate-timestamp detection in `parse_file` — duplicate bars for the same timestamp could silently corrupt backtesting results.
- F10: `_PARSER_REGISTRY` is a plain dict with no thread safety — not relevant until concurrent usage (e.g., FastAPI async workers).
- F11: `_read_lines` hardcodes UTF-8 encoding — non-UTF-8 files (Latin-1, Windows-1252) crash with no file-path context in the error.

## Deferred from: code review of 1-4-import-pipeline-core (2026-04-09)

- D1: Permission errors in `_discover_tickers` crash entire batch — `subdir.iterdir()` at line 219 has no exception handling, so an unreadable subdirectory aborts discovery for all tickers.
- D2: In-place ORM mutation in `_upsert_metadata` before persistence — `get_instrument_sync` returns an ORM object that is mutated directly before `upsert_instrument_sync`. If the upsert fails, dirty state remains on the session-attached object.

## Deferred from: code review of 1-5-cli-import-command-with-progress-and-summary (2026-04-09)

- D3: Broad `except Exception` in `_run_import` swallows errors with generic message — No traceback, no `--verbose`/`--debug` flag. Consistent with existing CLI commands but limits debuggability.
- D4: No `--dry-run` option — Architecture spec mentions it; planned for Story 1-6 (Pre-import Dry-run Validation).
- D5: `catalog_base_path` empty string defaults to cwd via `Path("")` — Pre-existing config default in `CatalogSettings`, not introduced by this diff.

## Deferred from: code review of 1-6-pre-import-dry-run-validation (2026-04-11)

- D6: `--dry-run` + `--timeframe` filter interaction is silently ignored — `--timeframe` has no effect on the dry-run path (scanner reports all timeframes found regardless). Spec's Testing Strategy "Not in this story" section explicitly lists this as follow-up work. Harmless until operators start pairing the flags in real workflows; add a warning or pass-through then.
- D7: `PARQUET_COMPRESSION_RATIO = 0.35` is an untuned placeholder — Both the constant and its docstring explicitly say "tune against one real FirstRate Stocks sample". Shipping the confident-looking "Est. Parquet Size" column against an untuned multiplier risks misleading disk-provisioning decisions. Tune once a real sample is available.
- D8: `stock_dividends/` and `stock_splits/` files pollute the Schema Mismatches table when dry-running the `Stocks/` root — E2E dry-run against `/Users/allay/Data/Stocks` on 2026-04-11 surfaced 6,505 "unrecognized filename pattern" mismatches, all from the dividends/splits subdirs that Epic 4 handles. The scanner is correct per AC-3, but the noise obscures real schema problems and may mislead operators into thinking the delivery is broken. Options when addressed: (a) skip known supplementary subdir names (`stock_dividends`, `stock_splits`) in `scan_firstrate_directory`, (b) fold the dividends/splits patterns into `_FILENAME_TIMEFRAME_MAP` as dedicated buckets once Story 4.1 lands, or (c) add a `--ignore-unknown` CLI flag. Natural fit for Story 4.1 (dividend/split parsing) or a small follow-up to 1.7.

## Deferred from: code review of 1-7-idempotent-import-and-failed-import-recovery (2026-04-11)

- ~~D9: RESOLVED (2026-04-12) — Added `bar_count_5min` column to `catalog_instruments` (migration `67772db31d8d`). `_TIMEFRAME_FIELD_MAP` now keys on `"{step}-{aggregation}"` (e.g., `"5-MINUTE"`) instead of just aggregation, so 1-min and 5-min resolve to distinct columns. Unknown timeframes still fall back to `bar_count_daily`.~~
- D10: `ImportService._classify_ticker` reads `existing.date_range_end` / `bar_count_*` off a potentially-detached SQLAlchemy instance (`src/services/firstrate/import_service.py:333`). The sync session is shared across tickers and across the full timeframe loop in `_run_import`; a long batch could see stale / expired attributes raise `DetachedInstanceError`. Pre-existing pattern (same concern applies to `_upsert_metadata`'s `get_instrument_sync` call); revisit when D2 is addressed.
- D11: `_classify_ticker`'s `source_day < metadata_day` branch logs a warning and reimports, silently overwriting known-good metadata with a truncated source. Spec Dev Notes lists this as intentional ("reimported + warn (stale/regressed source)") but in practice a warning can be missed in a 500-ticker batch. Consider hard-failing or requiring `--force` once such a flag is introduced. (`src/services/firstrate/import_service.py:361-370`)
- D12: `determine_exit_code` returns 0 on an empty results list (`src/cli/commands/import_data.py:98-100`) — CI/cron cannot distinguish "broken ticker discovery" from "clean all-skipped re-run". Overlaps with D1 (permission errors in `_discover_tickers`). Fix together once discovery gets proper error surfacing.

## Deferred from: code review of 2-1-explorer-page-with-ticker-list (2026-04-12)

- W1: `bar_count_5min` not persisted in `CatalogInstrumentRepository.upsert()` — both async and sync upsert methods omit `bar_count_5min` from the update field list. 5min counts show 0 for instruments upserted before fix. Pre-existing since migration `67772db31d8d`.
- W2: Legacy `CatalogInstrumentRepository.search()` uses substring ILIKE (`%query%`) instead of prefix match (`query%`). Not used by Story 2-1 code paths but inconsistent with the new `list_by_catalog_with_search()`.
- W3: Sort headers in ticker_list.html only support ascending order — no toggle to descending. Not in Story 2-1 task scope.

## Deferred from: code review of 2-3-data-statistics-panel (2026-04-19)

- F5: Duplicate `query_bars` on every chart-panel render (chart windowed + stats full-range). Intentional per Task 6.1 to satisfy "OOB chart + stats atomically in one response"; spec explicitly acknowledges the perf cost. Revisit with Parquet row-group min/max pushdown or a cached stats snapshot.
- F6: Unbounded 1m full-range scan (~1.95M bars for 5-year 1-min tickers) in `_build_ticker_stats`. Spec Known Perf Note flags as out-of-scope for Phase 1 (correctness over optimization). Address with row-group statistics pushdown or a precomputed min/max column.
- ~~F7: RESOLVED (2026-04-19, Epic 2 retro B2) — `catalog_service.query_bars()` calls wrapped in `asyncio.to_thread` at all 5 async call sites (`src/api/stats_service.py`, `src/api/rest/explorer.py`, `src/api/ui/explorer.py`, `src/api/rest/indicators.py` ×2) plus the internal call from `DataCatalogService.fetch_or_load` in `src/services/data_catalog.py`. `src/api/rest/timeseries.py` left as-is (sync FastAPI handler, runs on threadpool). 571 component tests pass.~~
- F8: `tf` coercion does not normalize case or whitespace — `"1h"` / `"1H "` / `""` silently become `"D"`. Matches spec's "coerce to D" mandate but surprises users; add case/whitespace normalization before the `VALID_TF_LABELS` check.
- F9: `selected_tf` template variable not pinned as a string at the route level — `(selected_tf or "D") | tojson` in `explorer.html:~89` renders the enum name if an `ExplorerTimeframe` slips through. Pin to `.label` in the route.
- F10: `_compute_price_range` allocates two full lists of lows/highs before calling `min`/`max`; single-pass reduction halves memory on million-bar scans. Pairs naturally with F6.
- F11: `VALID_TF_LABELS = {tf.label for tf in ExplorerTimeframe}` defined independently in `src/api/stats_service.py`, `src/api/rest/explorer.py`, and `src/api/ui/explorer.py`. Centralize on `ExplorerTimeframe`.
- F12: `TickerStatsResponse.price_min` / `price_max` typed as `float` rather than `Decimal`. Matches the project's existing price-shape convention but pushes IEEE-754 precision into the REST surface.
- F13: Datetime TZ ambiguity — naive datetimes serialize without `Z` suffix via Pydantic's default. REST consumers get ambiguous timestamps. Pre-existing; fix with a model `json_encoders` or strict tzinfo coercion upstream in metadata import.
- F14: `format_bar_count` passed as a callable in per-render template context rather than registered as a Jinja filter (pattern inherited from Story 2-2). Any new caller of `stats_panel.html` must remember to include it in context or the template raises `UndefinedError`.

## Deferred from: code review of 2-4-explorer-ux-polish (2026-04-19)

- U1: Concurrent overlapping HTMX requests to the same target can race — the retry-once handler may overwrite a successful newer response if an earlier request's retry completes second. Requires request-id/abort wiring across HTMX calls. Pre-existing pattern; newly surfaced by Story 2.4's error handler. (`templates/partials/htmx_error_handler.html`)
- U2: Deep-link stats auto-load + rapid ticker click can display stale stats on top of a new chart. Same abort-logic dependency as U1. (`templates/explorer/explorer.html` auto-load flow)
- U3: No arrow-key navigation between ticker rows / no roving tabindex — AC #10 only mandates Tab flow, so baseline passes; full WCAG grid/listbox keyboard model is a future refinement. (`templates/explorer/ticker_list.html`)
- U4: `aria-live="polite"` on `#stats-panel` announces the full 7-card stats grid to screen readers on every swap — verbose. Spec mandates `aria-live` placement; SR optimization explicitly out of scope per Story 2.4 Dev Notes. Consider `aria-busy` + summary announcement. (`templates/explorer/explorer.html:88`)
- U5: `role="button"` on `<tr>` conflicts with standard row/grid ARIA semantics (`aria-selected` is only valid under `role="listbox"`/`"grid"`/`"tree"`/`"tablist"`). Spec AC #11 mandates this shape; revisit when the explorer's list/grid role is formalized. (`templates/explorer/ticker_list.html:~109-113`)
- U6: Arrow-key rebinding guard (`data-nkbd="1"`) on `.timeframe-toolbar` and `.asset-class-pills` wrappers is effectively dead code — HTMX `innerHTML` swaps replace the wrapper node entirely, so the dataset flag is always fresh. Cleanup-only, no correctness impact. (`chart_panel.html`, `ticker_list.html` inline IIFEs)
- U7: Duplicate ~15-line arrow-key IIFE between `chart_panel.html` and `ticker_list.html`. Spec anti-pattern forbids a new `static/js/*.js` file, so duplication is intentional for this story. Consolidate when a site-wide keyboard helper is justified.
- U8: Component assertions for new ARIA/focus/retry markup are substring-only (`"aria-pressed=\"false\"" in text`, `"focus:ring-2" in text`, `"X-NTrader-Retry" in text`, event-listener name matches). Tests pass and catch obvious regressions but don't bind assertions to specific interactive elements. Refine when automated UI tests land. (`tests/component/api/test_explorer_routes.py` new tests)
- U9: `stats_panel_skeleton.html` hardcodes 7 cards with no structural link to the real stats grid — if the grid grows to 8 cards the skeleton silently drifts. No near-term grid churn planned.
- U10: Empty `data-ticker` fallback message reads `Failed to load chart data for selection.` — minor UX copy; revisit when a richer error-context model is introduced. (`templates/partials/htmx_error_handler.html`)

## Deferred from: code review of 3-1-catalog-integration-with-backtestengine (2026-04-19)

- R1: `execute_multi`'s `_extract_results` and `self._venue = first_venue` assume a single primary venue. Benign today because `execute_multi` is not reachable from CLI or web; revisit together with multi-instrument input wiring. (`src/core/backtest_orchestrator.py:219, 281-293`)
- R2: Load-phase (`load_backtest_data` including `catalog.bars()` Parquet scan) is not covered by `timeout_seconds`. A slow read holds `_backtest_lock` for its full duration. Pre-existing pattern — orchestration timeout only wraps `orchestrator.execute`. (`src/api/ui/backtests.py`)
- R3: `to_persistence_data_source()` produces `"catalog:<name>"` values that would fail `BacktestRequest.data_source` validator (`{catalog, ibkr, kraken, mock}`) on round-trip from the DB. No caller reconstructs a request from `backtest_runs.data_source` today; flag for when/if that path exists. (`src/models/backtest_request.py:104-111, 363-372`)
- R4: `_resolve_config_mode` updates `catalog_name` via `model_copy` but leaves `instrument_id` at whatever the YAML declared. YAML-mode catalog_name passthrough is an explicit Task 4.3 deferral. (`src/cli/commands/_backtest_helpers.py:226-227`)
- R5: Multi-instrument edge cases — duplicate ticker (second `add_instrument` raises), heterogeneous bar intervals (strategy only subscribes to primary bar_type), empty bars for secondary instrument (`engine.add_data([])` undefined behavior). All reachable only after `execute_multi` gets a user-facing input channel.
- R6: `catalog.bars(start=..., end=...)` may return bars slightly outside `[start, end]` at Parquet partition edges. Loader does not clip. Same behavior as the pre-existing legacy catalog read path.
- R7: Parity smoke test (AC #12) compares `CATALOG_BASE_PATH/e2e-test` against `NAUTILUS_PATH` — byte-identity is only meaningful if both env vars point at the same files. Informational/local only, no CI enforcement.
- R8: `make test-integration` still surfaces pre-existing Nautilus C-extension parallelism flakiness (17 failed on main, 15 failed on this branch per story completion notes). Task 9.3 checkbox is over-stated but no regressions introduced.
- R9: No end-to-end CLI or web test asserts the user-surfaced message / exit code / `execution_error` branch for AC #6. Only loader-level assertions exist today.
- R10: `_resolve_named_catalog_loader()` and `_create_strategy_multi()` are cosmetic indirections that add no real decoupling. `_build_equity(bars=None)` legacy branch is reachable only from tests. Refactor opportunities, not defects.
- R11: `execute_multi` engine-layer plumbing is Nautilus-native-correct (verified against `backtest_book_imbalance_betfair` tutorial) but has no user-facing input channel — `BacktestRequest.symbol: str` is scalar, CLI `--symbol` is single-valued, web form has one symbol input. AC #5 is exercised by integration tests only. Input-channel wiring (`BacktestRequest.symbols: list[str]`, repeatable CLI flag, multi-select web form) is a Story 3.2 concern alongside the explorer→backtest bridge.
