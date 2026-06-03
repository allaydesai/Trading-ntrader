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

- ~~R1~~: Resolved 2026-05-10 by Epic 3 retro action C4 — `execute_multi` deleted; concern no longer applies.
- R2: Load-phase (`load_backtest_data` including `catalog.bars()` Parquet scan) is not covered by `timeout_seconds`. A slow read holds `_backtest_lock` for its full duration. Pre-existing pattern — orchestration timeout only wraps `orchestrator.execute`. (`src/api/ui/backtests.py`)
- R3: `to_persistence_data_source()` produces `"catalog:<name>"` values that would fail `BacktestRequest.data_source` validator (`{catalog, ibkr, kraken, mock}`) on round-trip from the DB. No caller reconstructs a request from `backtest_runs.data_source` today; flag for when/if that path exists. (`src/models/backtest_request.py:104-111, 363-372`)
- R4: `_resolve_config_mode` updates `catalog_name` via `model_copy` but leaves `instrument_id` at whatever the YAML declared. YAML-mode catalog_name passthrough is an explicit Task 4.3 deferral. (`src/cli/commands/_backtest_helpers.py:226-227`)
- ~~R5~~: Resolved 2026-05-10 by Epic 3 retro action C4 — `execute_multi` deleted; multi-instrument edge cases moot.
- R6: `catalog.bars(start=..., end=...)` may return bars slightly outside `[start, end]` at Parquet partition edges. Loader does not clip. Same behavior as the pre-existing legacy catalog read path.
- R7: Parity smoke test (AC #12) compares `CATALOG_BASE_PATH/e2e-test` against `NAUTILUS_PATH` — byte-identity is only meaningful if both env vars point at the same files. Informational/local only, no CI enforcement.
- R8: `make test-integration` still surfaces pre-existing Nautilus C-extension parallelism flakiness (17 failed on main, 15 failed on this branch per story completion notes). Task 9.3 checkbox is over-stated but no regressions introduced.
- R9: No end-to-end CLI or web test asserts the user-surfaced message / exit code / `execution_error` branch for AC #6. Only loader-level assertions exist today.
- R10: `_resolve_named_catalog_loader()` is a cosmetic indirection that adds no real decoupling. `_build_equity(bars=None)` legacy branch is reachable only from tests. Refactor opportunities, not defects. (Original third item — `_create_strategy_multi()` — resolved 2026-05-10 by retro C4.)
- ~~R11~~: Resolved 2026-05-10 by Epic 3 retro action C4 — `execute_multi` deleted; the missing input channel will not be wired (IBKR data layer covers multi-asset at the bar level instead).

## Deferred from: code review of 3-2-explorer-to-backtest-bridge (2026-04-20)

- B1: Explorer state params (`search`, `asset_class`, `sort_by`, `page`) flow into the bridge URL without allow-list validation or length caps. Pre-existing Epic 2 concern; Story 3.2 just threads them through unchanged. (`src/api/ui/explorer.py` `chart_panel_fragment`)
- B2: `date_range_start/end.strftime("%Y-%m-%d")` has no timezone normalization; a tz-naive legacy row would produce a date in the DB's implicit zone rather than UTC. `CatalogInstrument.date_range_*` columns are `TIMESTAMP(timezone=True)` in practice, so low-risk. (`src/api/ui/explorer.py` `_build_run_backtest_url`)
- B3: `data_source.split(":", 1)[1]` assumes no colon inside catalog names. Catalog-name validator rejects non-`[A-Za-z0-9_-]` at import time, so colons cannot reach this branch. Theoretical only. (`src/api/ui/backtests.py:590-592`)
- B4: `bar_type[:-len("-LAST")]` assumes the `-LAST` aggregation suffix always applies. All current bar types are LAST-aggregated; BID/ASK/MID aggregations are future concerns. (`src/api/ui/backtests.py:594`)
- B5: Fallback "Back to Explorer" URL does not preserve `search`/`asset_class`/`sort_by`/`page` when `explorer_return` is absent. Inherent limitation of the deterministic fallback; acceptable per AC #11. (`src/api/ui/backtests.py:157-162`)
- B6: `_BRIDGE_PARAM_MAP` relies on `form_data=raw_data` being used on the validation-error re-render path; a future refactor to `form_data=form.model_dump()` would silently drop `explorer_return` because it isn't a field on `BacktestRunFormData`. Latent footgun; spec explicitly chose to keep `explorer_return` out of the form model. Document or add a regression test later. (`src/api/ui/backtests.py` `_build_run_context`)

## Deferred from: code review of 3-3-backtest-verification-and-reference-comparison (2026-05-03)

- C3: `--skip-import` doesn't validate catalog integrity — stale data from a prior run with a different ticker/CSV silently feeds the comparison and produces meaningless verdicts.
- C4: `CSVLoader(conflict_mode="overwrite")` may leak stale parquet shards across harness runs with different CSV inputs — pre-existing CSVLoader semantic (overwrite is per-(instrument_id, bar_type), not directory-wide).
- C5: `output_dir.mkdir` failure raises bare `PermissionError` traceback rather than the codebase's `error_formatter` style. Cosmetic.
- C6: `Console(quiet=True)` in `_run_comparison_async` silences orchestrator warnings, making data divergences harder to debug. Verbosity trade-off.
- C7: PnL `1.0` floor in `evaluate_tolerance` changes "0.1% threshold" semantics for tiny absolute PnL — `pnl_max=max(abs(legacy), abs(firstrate), 1.0)`. Documented behavior, edge of soundness.
- C8: `BacktestResultSummary.final_balance` is `float` but the engine returns `Decimal` — both sides equally lossy, so the comparison is internally consistent, but the entire point of FirstRate is decimal preservation. Existing pattern.
- C9: Mock `cache.instrument()` accepts any argument in sizing tests — does not validate the lookup key, but covered by integration tests.
- C10: `orchestrator.dispose()` exception masking in `_execute_and_summarise:187-190` — if dispose raises, it propagates from `finally` and masks any in-flight backtest exception. Nautilus dispose rarely raises in practice; each invocation has its own orchestrator instance so cross-call contamination is bounded.
- C11: Renderer hard-codes display thresholds (`"threshold 0.50%"`, `"0.10%"`) — accurate for Phase 1 fixed values, would mislead if `evaluate_tolerance` is called with custom tolerances. `ComparisonReport` lacks a `thresholds` field. (`src/services/comparison_renderer.py:84-96`)

## Deferred from: code review of 3-4-ibkr-vs-firstrate-parity-comparison (2026-05-05)

- D1: Existing FirstRate-imported catalogs are 4–5h timestamp-shifted by the prior parser bug; require re-import after the fix. Already tracked as Story 3.6 follow-up. (`src/services/firstrate/parsers/firstrate_csv_parser.py`)
- D2: `_firstrate_metadata_count` hardcodes `bar_count_minute` — dormant while the harness pins `TIMEFRAME_SPEC=1-MINUTE-LAST`; needs timeframe-aware lookup if hourly/daily variants of the parity harness are added. (`scripts/verify_aapl_2018_reference.py:434`)
- D3: Auto-rotation triggers on benign socket `TimeoutError` in Python 3.11+ (`asyncio.TimeoutError = TimeoutError`) — a network blip burns through `max_id_rotations` client_ids. Differentiation requires exception-source inspection or a separate fast-fail path. (`src/services/ibkr_client.py:240-247`)
- D4: Diagnostic bash scripts (`run_kill_scenarios.sh`, `run_reconnect_scenarios.sh`) use only `set -u`, not `set -e` — `kill -9` / `wait` failures silently pass. Diagnostic-only; not CI-consumed. (`scripts/diagnostics/run_*.sh`)
- D5: `_fetch_ibkr_chunked` has no per-chunk error tolerance — a single bad chunk aborts the whole run; cached chunks survive in the catalog. Acceptable for a one-time evidence harness. (`scripts/verify_aapl_2018_reference.py:252-289`)
- D6: Daily-bar timestamps shifted from midnight UTC to midnight ET (= 05:00 UTC) by the TZ fix — semantic change for the entire FirstRate parser. No daily-bar-specific regression test added; covered by Follow-up #1 re-import. (`src/services/firstrate/parsers/firstrate_csv_parser.py:194`)
- D7: `_dedup_by_ts_event` keeps "first" occurrence — order from `query_bars` is unspecified; latent risk if Nautilus ever returns subtly-different OHLCV at chunk boundaries. No consistency check (e.g. comparing duplicate OHLCV before discarding). (`scripts/verify_aapl_2018_reference.py:188-204`)
- D8: Symmetric intersection silently drops legitimate divergence evidence — by design (logged at INFO via `timestamp_intersection_built`); real risk is that the parity report passes when one source is structurally missing days. (`scripts/verify_aapl_2018_reference.py:511-525`)
- D9: ns-level `ts_event` equality assumed across IBKR ∩ FirstRate — dormant for 1-MINUTE (both produce nanosecond-multiple-of-10⁹); latent for 1-SECOND/TICK timeframes. No tolerance window on the intersection. (`scripts/verify_aapl_2018_reference.py:176-185`)
- D10: DST non-existent (spring-forward gap) and ambiguous (fall-back overlap) local times silently coerced by `replace(tzinfo=ZoneInfo("America/New_York"))` (PEP-495 `fold=0` default). Dormant for RTH-only equities; harden alongside future 24/7 asset class story when FirstRate's fall-back convention is documented. (`src/services/firstrate/parsers/firstrate_csv_parser.py:194`, `src/services/firstrate/source_probe.py:117`)

## Deferred from: Story 3-6 verify-and-close (2026-06-03)

- E1: **Story 3-6 Task 7 — IBKR-vs-FirstRate parity re-run** deferred (non-blocking for Epic 4). Requires IBKR Gateway + `IBKR_AVAILABLE=1 E2E_CATALOG_AVAILABLE=1`; runs `pytest tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py --forked`. Parity was already characterized in Story 3-4 (post-fix: 0% bar Δ, +44 trade Δ, 0.31% PnL Δ, all within widened tolerances). This re-run only re-confirms parity against the fully re-imported catalog and folds into the **Story 3-7** residual-drift investigation (which is the natural place to run it, since 3-7 already needs IBKR + a single-venue contract spike).
