# Story 3.2: Explorer-to-Backtest Bridge

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want a "Run Backtest" button in the explorer chart panel that deep-links into the existing backtest run page with catalog, ticker, timeframe, and date range pre-populated,
So that I can transition from data verification to backtest configuration without re-entering context I just confirmed in the explorer.

## Scope & Non-Goals

**In scope:**

- Primary-action "Run Backtest" button rendered inside the chart panel header (`templates/explorer/chart_panel.html`) whenever a ticker is selected and a chart has loaded. Links to `/backtests/run?catalog=<name>&ticker=<sym>&timeframe=<1-DAY|…>&start=<YYYY-MM-DD>&end=<YYYY-MM-DD>` with the full ticker metadata date range from `CatalogInstrument.date_range_{start,end}`.
- `GET /backtests/run` handler in `src/api/ui/backtests.py` — extend `_build_run_context` to consume the four new explorer query params (`ticker`, `timeframe`, `start`, `end`) in addition to the existing `?catalog=` pre-fill (Story 3.1 Task 6.5). Symbol field, start/end date fields, and timeframe dropdown must be pre-populated but remain editable. Validator-invalid query values are silently ignored (fall back to defaults, not 500).
- Timeframe translation: explorer uses `ExplorerTimeframe.label ∈ {D, 1H, 5m, 1m}` / `bar_type_spec ∈ {1-DAY-LAST, 1-HOUR-LAST, 5-MINUTE-LAST, 1-MINUTE-LAST}`; the backtest run form uses `VALID_TIMEFRAMES ∈ {1-MINUTE, 5-MINUTE, 15-MINUTE, 1-HOUR, 4-HOUR, 1-DAY, 1-WEEK}` (`src/api/models/run_backtest.py:14-22`). Bridge URL emits the run-form-compatible form (`1-DAY`, not `D` / not `1-DAY-LAST`) — the backtest page does NOT learn to accept `D`.
- Round-trip "Back to Explorer" link on the backtest detail page (`templates/backtests/detail.html` action-button row) that reconstructs explorer state. The bridge carries an `explorer_return` query param (URL-encoded) from the chart-panel button → run form → detail page so the detail-page link preserves catalog + search + asset-class filter + selected ticker + selected timeframe. Falls back to `/explorer?catalog=<run.catalog_name>&ticker=<run.symbol>&tf=<explorer-label>` when `explorer_return` is absent (e.g., direct POST to `/backtests/run`).
- No new REST endpoints. No new service-layer code. No new models. This story is template/routing plumbing on existing scaffolding.
- **Backward compatibility hard requirement:** Users who reach `/backtests/run` with no query params (direct nav, typed URL, form re-submit after validation error) must see the exact same form they saw before this story. All new query params are optional with no defaults leaking into non-bridge flows.

**Out of scope:**

- Multi-ticker selection in the explorer → multi-instrument backtest bridge (Deferred R11: `execute_multi` exists at the engine layer but `BacktestRequest.symbol: str` is scalar; multi-ticker input channel remains out of scope and is the natural next step after 3.3). Bridge carries exactly one ticker.
- New explorer routes or HTMX fragments. The button is a plain `<a href>` — no `hx-get`, no `hx-push-url`. Navigation is full-page.
- Reference/parity dataset comparison — that's Story 3.3 (AAPL 2018 1-MINUTE).
- Asset-appropriate position sizing per asset class — Story 3.3 owns that concern.
- A catalog `<select>` dropdown on the backtest run page (currently read-only input from Story 3.1 — kept read-only here). Switching catalogs happens through the explorer's catalog selector, not the run form. Story 3.2 does not promote the field to a dropdown.
- "Last explorer state" persistence beyond URL params — no session cookies, no backend session state, no localStorage. State lives in the `explorer_return` query param for exactly one round-trip.
- Any reshuffling of the chart panel layout, new stat cards, or toolbar re-organization beyond adding the button.

## Acceptance Criteria

1. **Button renders when a ticker is selected** — **Given** the explorer page with a catalog selected, a ticker clicked, and the chart panel populated (i.e., the `hx-get /explorer/chart-panel` swap has completed), **When** the chart panel renders, **Then** a "Run Backtest" button with `bg-blue-500 text-white` styling and `aria-label="Run backtest for {ticker}"` is displayed in the chart panel header (right-aligned, same row as the timeframe toolbar's ticker/bar count label), **And** the button is a plain `<a href>` element (not a `<button>`, not HTMX) so middle-click / ⌘-click open in a new tab.

2. **Button is hidden when no ticker is selected** — **Given** the explorer page with no ticker selected (the chart-panel `<div>` is empty — no `hx-trigger="load"` fired), **When** the explorer page renders, **Then** the "Run Backtest" button is NOT present in the DOM, **And** the button is never shown as "disabled" — its absence comes from the chart panel fragment not being rendered at all (existing behavior from Story 2-1/2-2 where the chart panel is empty until a ticker is clicked).

3. **Button URL is correctly constructed** — **Given** a ticker `SPY` selected in catalog `firstrate-research` at timeframe `D` with `instrument.date_range_start = 2003-01-02` and `instrument.date_range_end = 2024-12-31`, **When** the "Run Backtest" button is clicked, **Then** the browser navigates to `/backtests/run?catalog=firstrate-research&ticker=SPY&timeframe=1-DAY&start=2003-01-02&end=2024-12-31&explorer_return=<url-encoded-explorer-state>`, **And** all values are URL-encoded (tickers like `BRK.B` survive verbatim; `.` does not require encoding but Unicode tickers would), **And** `timeframe` uses the RUN-FORM shape (`1-DAY`, `1-HOUR`, `5-MINUTE`, `1-MINUTE`) — NOT the explorer label (`D`, `1H`, `5m`, `1m`) and NOT the raw bar_type_spec (`1-DAY-LAST`).

4. **Timeframe mapping covers every explorer timeframe** — **Given** each `ExplorerTimeframe` member (`DAILY → D`, `HOURLY → 1H`, `FIVE_MIN → 5m`, `ONE_MIN → 1m`), **When** the bridge URL is built, **Then** the emitted `timeframe=` query param is exactly one of `{1-DAY, 1-HOUR, 5-MINUTE, 1-MINUTE}` — all four of which are present in `VALID_TIMEFRAMES` in `src/api/models/run_backtest.py:14-22`, **And** there is no silent fallback to `1-DAY` for an unrecognized explorer timeframe (the mapping is explicit in code, a new explorer timeframe must be mapped explicitly or cause a `KeyError` at render time — caught in tests).

5. **Date range from DB metadata, ISO-formatted** — **Given** an `instrument.date_range_start` and `instrument.date_range_end` of type `datetime` (naive or aware) populated from `CatalogInstrument`, **When** the bridge URL is built, **Then** `start=` and `end=` are formatted as `YYYY-MM-DD` (date portion only, no time, no timezone suffix) matching the existing run-form `<input type="date">` accepted shape, **And** when either `date_range_start` or `date_range_end` is `None` (metadata-less ticker edge case), the button is still rendered but the corresponding query param is omitted — the run form will fall back to its default empty input.

6. **Run form pre-populates from all four query params** — **Given** a GET to `/backtests/run?catalog=firstrate-research&ticker=SPY&timeframe=1-DAY&start=2003-01-02&end=2024-12-31`, **When** the form renders, **Then** the Symbol input is pre-filled with `SPY`, the Start Date input with `2003-01-02`, the End Date input with `2024-12-31`, the Timeframe dropdown's `<option value="1-DAY">` is `selected`, the Catalog (readonly) input shows `firstrate-research`, **And** the Strategy dropdown is unchanged (`<option value="">Select a strategy...</option>` selected) — Story 3.2 does NOT pre-select a strategy.

7. **Invalid / tampered query params do not 500** — **Given** a GET to `/backtests/run?ticker=<script>alert(1)</script>&timeframe=DANGEROUS&start=not-a-date&end=2024-13-99`, **When** the form renders, **Then** the page renders with status 200, the Symbol input contains the raw string (rendered through Jinja's autoescape — not executed), the Timeframe dropdown ignores the invalid value and defaults to the existing `1-DAY` default, the Start/End date inputs fall back to empty (existing behavior for malformed dates), **And** no server-side error is raised — all pre-fill reads are defensive (`request.query_params.get("…")` with falsy fallback).

8. **Catalog name validation is still enforced** — **Given** a GET to `/backtests/run?catalog=%20whitespace%20` or `?catalog=has spaces&ticker=SPY`, **When** the form renders, **Then** the catalog_name pre-fill runs through the existing Story 3.1 `BacktestRequest.catalog_name` validator at POST time (whitespace-only normalized to None, non-`[A-Za-z0-9_-]` rejected per Story 3.1 Review Patch) — if the user submits the form with an invalid catalog name, the POST returns a validation error surfaced inline (existing pattern), **And** the GET render does NOT pre-validate — the invalid value sits in the readonly input and only fails on submit. This preserves "never 500 on a tampered GET".

9. **POST preserves `explorer_return` for the round-trip** — **Given** a form submission that originated from the bridge (GET had `?explorer_return=<url>`), **When** the user clicks "Run Backtest" in the form, **Then** the hidden `explorer_return` field is submitted along with the other form data, **And** on successful completion the handler's `HX-Redirect` header points to `/backtests/{run_id}?explorer_return=<url>` (URL-encoded once — not double-encoded) — **And** on validation / execution / timeout error the re-rendered form carries the same `explorer_return` hidden field (so the user's eventual successful run still preserves the round-trip).

10. **"Back to Explorer" link renders on the detail page** — **Given** a backtest detail page (`GET /backtests/{run_id}`) loaded with a query string containing `?explorer_return=<url>`, **When** the page renders, **Then** a "Back to Explorer" link is displayed in the action-button row (`templates/backtests/detail.html`), styled as a secondary button (`border-slate-700 text-slate-400 hover:text-slate-200 px-4 py-2 rounded`), **And** its `href` equals the decoded `explorer_return` value — subject to an allow-list safety check that the URL path starts with `/explorer` (prevents open redirect via `?explorer_return=https://evil.example.com`), **And** when the check fails the link falls back to the deterministic construction from AC #11.

11. **Fallback "Back to Explorer" URL when `explorer_return` is absent** — **Given** a backtest detail page loaded WITHOUT `?explorer_return=…` (direct navigation, old backtest, or the URL was dropped by the user), **When** the page renders, **Then** the "Back to Explorer" link is still shown, pointing to `/explorer?catalog={run.catalog_name}&ticker={run.symbol}&tf={explorer_label}`, where `explorer_label` is the reverse mapping of the stored timeframe (e.g., `1-DAY → D`, `1-HOUR → 1H`; falls back to omitting `tf=` when no mapping exists), **And** when `run.catalog_name` is not set (non-named-catalog backtest), the link is `/explorer` with no query params.

12. **Open-redirect guard on `explorer_return`** — **Given** a crafted URL like `/backtests/{id}?explorer_return=https://evil.example.com/phish` or `?explorer_return=//evil.example.com/path` or `?explorer_return=javascript:alert(1)`, **When** the detail page renders, **Then** the guard rejects any value that does not begin with a literal `/explorer` or `/explorer?` (URL-decoded, then checked against a strict prefix), **And** the link falls back to the AC #11 fallback construction, **And** a structured log line `explorer_return rejected: value=<...>, reason=prefix_mismatch` is emitted at WARNING level for observability (not an error — tampering is not a server bug).

13. **Explorer state in `explorer_return` is the full current explorer URL** — **Given** the user navigated to `/explorer?catalog=firstrate-research&search=SPY&asset_class=ETF&ticker=SPY&tf=D&page=2&sort_by=ticker` before clicking "Run Backtest", **When** the bridge URL is constructed on the server during `GET /explorer/chart-panel`, **Then** the chart-panel template has the ExplorerPageState (`state.search`, `state.asset_class`, `state.page`, `state.sort_by`, `state.catalog`) already in context — but the chart panel is loaded as a fragment on ticker click and does NOT have the full page URL; so the button encodes state from context variables: `/explorer?catalog={catalog}&ticker={ticker}&tf={active_tf.label}` plus, when available, `&search={state.search}&asset_class={state.asset_class}&sort_by={state.sort_by}&page={state.page}`, **And** the `state` object is explicitly threaded from `explorer_page` / `chart_panel_fragment` into the chart-panel template — not obtained from browser `window.location` (which would require client-side JS and break SSR/noscript). **Simplification note for implementation:** the chart-panel fragment currently receives `catalog` + `ticker` + `active_tf` but NOT the full `ExplorerPageState`; the chart-panel route must accept `search`, `asset_class`, `sort_by`, `page` as optional query params (matching how the explorer page passes them) and thread them into the template, or set them from `request.query_params` defensively. See Critical Implementation Details.

14. **Keyboard + accessibility** — **Given** the Run Backtest button is rendered, **When** the user tabs through the chart panel, **Then** focus moves `timeframe buttons → Run Backtest button → (out of chart panel)`, **And** the button has `aria-label="Run backtest for {ticker}"`, **And** a visible focus ring via Tailwind `focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-950` matching existing Story 2-4 focus patterns. On the detail page, the "Back to Explorer" link must also be keyboard-reachable and have the same focus-ring pattern.

15. **No regressions in explorer or backtest flows** — All existing tests under `tests/component/api/test_explorer_routes.py`, `tests/component/api/test_chart_panel_routes.py`, `tests/component/api/test_run_backtest_routes.py`, `tests/ui/test_explorer_errors.py`, `tests/ui/test_backtest_detail.py` continue to pass without modification. The chart panel still renders with no "Run Backtest" button on deep-linked deep paths if the DB returns no ticker (existing 404 path, unchanged).

## Tasks / Subtasks

- [x] Task 1: Chart-panel template — render "Run Backtest" button (AC: #1, #2, #3, #14)
  - [x] 1.1 Failing component test in `tests/component/api/test_chart_panel_routes.py::TestRunBacktestButton::test_button_rendered_with_correct_url`.
  - [x] 1.2 Failing component test `test_button_absent_when_chart_panel_not_loaded` covered by sibling `test_button_absent_when_chart_panel_not_loaded`.
  - [x] 1.3 Failing component test `test_button_url_encodes_special_ticker` — `BRK.B` ticker survives verbatim.
  - [x] 1.4 Edited `templates/explorer/chart_panel.html` — anchor sits in a sibling `<div class="ml-auto flex items-center gap-3">` outside the `role=toolbar` wrapper.
  - [x] 1.5 Anchor wrapped in `{% if run_backtest_url %}` — hidden when route returns None (zero bars).

- [x] Task 2: `chart_panel_fragment` route — build the bridge URL (AC: #3, #4, #5, #13)
  - [x] 2.1 Parametrised unit tests for each explorer timeframe → run-form timeframe mapping.
  - [x] 2.2 `test_build_run_backtest_url_missing_date_range` — URL omits `start`/`end` when metadata is None.
  - [x] 2.3 `test_build_run_backtest_url_zero_bars` — returns `None` when `bar_count <= 0`.
  - [x] 2.4 `test_build_run_backtest_url_includes_explorer_return` — encoded explorer state, URL length < 2000.
  - [x] 2.5 Implemented `_build_run_backtest_url(...)` in `src/api/ui/explorer.py` — uses `urllib.parse.urlencode`.
  - [x] 2.6 Added module-level `_TIMEFRAME_EXPLORER_TO_RUN_FORM` — `KeyError` on unmapped label.
  - [x] 2.7 `chart_panel_fragment` accepts `search`, `asset_class`, `sort_by`, `page` optional query params and threads them into the builder; `run_backtest_url` added to template context.
  - [x] 2.8 Extended `templates/explorer/explorer.html` `hx-vals` for the chart-panel trigger to include explorer state fields. Also added to timeframe-button `hx-vals` so tf switches preserve state.

- [x] Task 3: Run-form route — consume all bridge query params (AC: #6, #7, #9)
  - [x] 3.1 Passing component test `TestBridgePreFill::test_full_bridge_url_prefills_all_fields`.
  - [x] 3.2 Passing component test `test_invalid_bridge_params_do_not_500` — XSS escaped, unknown timeframe silently drops.
  - [x] 3.3 Passing component test `test_bridge_without_explorer_return` — hidden field absent when no explorer_return URL hint (cleaner than rendering empty value).
  - [x] 3.4 Extended `_build_run_context` with `_BRIDGE_PARAM_MAP` + `explorer_return` handling — preserves "only pre-fill when empty" guard.
  - [x] 3.5 Added hidden `explorer_return` input in `run.html` gated on truthy value.
  - [x] 3.6 POST handler reads `explorer_return` from form into `raw_data` — survives validation/execution retry re-renders via `_build_run_context(form_data=raw_data)`.

- [x] Task 4: POST-success redirect carries `explorer_return` (AC: #9, #10)
  - [x] 4.1 Passing component test `test_post_success_redirects_with_explorer_return` + `test_post_success_without_explorer_return_unchanged` regression guard.
  - [x] 4.2 Success branch appends `?explorer_return=<urlencode>` — used `urlencode` (not `quote`) for consistency with the rest of the code and because `urlencode` on a list-of-tuples handles `/` and `&` correctly in the value.
  - [x] 4.3 explorer_return rides with the re-rendered form (Task 3) and ONLY the success redirect appends it to the detail URL.

- [x] Task 5: Detail page — render "Back to Explorer" link (AC: #10, #11, #12, #14)
  - [x] 5.1 Passing component test `TestBackToExplorerLink::test_link_rendered_with_valid_explorer_return` in NEW file `tests/component/api/test_backtest_detail_routes.py`.
  - [x] 5.2 Parametrised `test_link_rejects_open_redirect` covers all malicious URL shapes.
  - [x] 5.3 Covered by 5.2 — parametrised over `https://`, `//`, `javascript:`, `data:`, `../`.
  - [x] 5.4 `test_link_fallback_without_catalog_name` — returns exactly `/explorer`.
  - [x] 5.5 Covered by unit tests `test_reverse_timeframe_map` + `test_link_fallback_uses_catalog_from_data_source` (integration).
  - [x] 5.6 Implemented `_resolve_back_to_explorer_url` — prefix-validates `/explorer` or `/explorer?`, rejects protocol-relative, logs structured WARNING.
  - [x] 5.7 Route reads `request.query_params.get("explorer_return")` and passes it + `_view_run_for_resolver(backtest)` to `_resolve_back_to_explorer_url`.
  - [x] 5.8 Link rendered in `templates/backtests/detail.html` as the LEFTMOST action with secondary-button styling + Story 2-4 focus ring.
  - [x] 5.9 Resolver kept pure — `_view_run_for_resolver` adapts the ORM row to flat `catalog_name`/`symbol`/`timeframe` fields. `to_detail_view` was NOT modified — we pass `back_to_explorer_url` in the template context alongside `view`, keeping the view model focused on metrics/configuration.

- [x] Task 6: Reverse timeframe mapping for the detail-page fallback (AC: #11)
  - [x] 6.1 Parametrised `test_reverse_timeframe_map` + `test_reverse_timeframe_map_unmapped_returns_none`.
  - [x] 6.2 Added `_TIMEFRAME_RUN_FORM_TO_EXPLORER` as module-level constant in `src/api/ui/explorer.py`; imported into `backtests.py` for the resolver.
  - [x] 6.3 Confirmed — mapping contains exactly D/1H/5m/1m; `4-HOUR` / `1-WEEK` unmapped → resolver omits `tf=`.

- [x] Task 7: UI smoke via `agent-browser` (AC: #1, #2, #6, #10, #14)
  - [x] 7.1 Dev server + CSS rebuild run; Tailwind classes verified visually in screenshots.
  - [x] 7.2 `agent-browser open /explorer?catalog=e2e-test` — 5 tickers loaded (AAPL/AMZN/MSFT/NVDA/TSLA), snapshot captured.
  - [x] 7.3 Clicked AAPL row → HTMX swap completed, anchor with `aria-label="Run backtest for AAPL"` rendered. Verified `href="/backtests/run?catalog=e2e-test&ticker=AAPL&timeframe=1-DAY&start=2000-01-03&end=2026-04-02&explorer_return=..."`. Screenshot: `/tmp/story-3-2-evidence/01-explorer-button-visible.png`.
  - [x] 7.4 Clicked anchor → full-page nav to `/backtests/run` with all fields pre-populated (Symbol=AAPL, start=2000-01-03, end=2026-04-02, Timeframe=1-DAY selected, Catalog=e2e-test, hidden explorer_return=`/explorer?catalog=e2e-test&ticker=AAPL&tf=D`). Screenshot: `02-run-form-prefilled.png`.
  - [x] 7.5 Picked `sma_crossover`, shortened dates to 2024-01-01 → 2024-03-31, submitted. HX-Redirect landed on `/backtests/{run_id}?explorer_return=...`. Detail page rendered "Back to Explorer" as leftmost action with `href="/explorer?catalog=e2e-test&ticker=AAPL&tf=D"`. Screenshot: `03-detail-back-link.png`. Clicked it → landed on `/explorer?catalog=e2e-test&ticker=AAPL&tf=D` with AAPL selected and D timeframe active. Screenshot: `04-back-to-explorer-restored.png`.
  - [x] 7.6 Open-redirect test: navigated to `/backtests/{id}?explorer_return=https://evil.example.com/phish` — link fell back to `/explorer?catalog=e2e-test&ticker=AAPL`; confirmed no occurrence of "evil.example.com" anywhere in the rendered DOM.

- [x] Task 8: Quality gates (AC: #15)
  - [x] 8.1 `make format && make lint && make typecheck` — all clean.
  - [x] 8.2 `make test-unit` → 811 passing (baseline 776, +35 new bridge unit tests). `make test-component` → 608 passing + 16 pre-existing skips (baseline 586, +22 new bridge component tests across chart-panel, run form, and detail page).
  - [x] 8.3 No new integration tests — bridge is pure routing/template layer. Skipped integration run since no bridge path hits Nautilus C extensions.

### Review Findings

_Code review 2026-04-20 — three parallel layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Auditor: all 15 ACs satisfied, no spec violations, no scope creep._

**Decision needed** — resolved 2026-04-21:

- [x] [Review][Decision→Patch] Fallback `tf=` is always omitted in production — resolved as patch (option 2): extend `StrategyConfigSnapshot` with `bar_type: str | None = None`; update `src/core/backtest_orchestrator.py` (`_persist_successful` ~504, `_persist_failed` ~584) and `src/core/backtest_runner.py` (112, 263) to set it before validation. `_view_run_for_resolver` already reads `cfg.get("bar_type")` — no change there. Old rows keep `bar_type=None` and degrade to omitting `tf=` per AC #11 (acceptable).
- [x] [Review][Decision→Patch] Bridge end-date emits `YYYY-MM-DD`, backend combines with midnight UTC — resolved as patch (option 2): change the POST handler end-of-day combine at `src/api/ui/backtests.py:253` from `datetime.min.time()` to `datetime.max.time()` so `end_date=2024-12-31` maps to `2024-12-31 23:59:59.999999 UTC`. Fixes every caller (bridge + direct URL + hand-typed). Re-run `make test-unit test-component` to catch any test that baked in the exclusive-midnight semantic.

**Patch** — all applied 2026-04-21:

- [x] [Review][Patch] Extend `StrategyConfigSnapshot` with `bar_type: str | None = None` and populate from `request.bar_type` in `_persist_results` + `_persist_failed` so the detail-page fallback can recover the explorer timeframe label on the web-UI flow. CLI-origin runs via `backtest_runner.py` remain unaffected (bar_type=None → fallback omits `tf=` per AC #11, no regression vs. prior behavior).
- [x] [Review][Patch] Change POST handler EOD combine at `src/api/ui/backtests.py` to `datetime.combine(form_data.end_date, datetime.max.time(), tzinfo=timezone.utc)` so `end_date=2024-12-31` includes bars on that date. Re-ran `make test-unit` (811 passing) and `make test-component` (608 passing); no regressions.
- [x] [Review][Patch] Open-redirect guard rejects embedded control chars — replaced inline prefix check in `_resolve_back_to_explorer_url` with a `_safe_explorer_return` helper that rejects any codepoint `< 0x20` or `== 0x7F` after `unquote().strip()`. Logs `reason=control_chars` vs `protocol_relative` vs `prefix_mismatch` for triage.
- [x] [Review][Patch] Fallback URL omits empty `ticker=` — reshape to append `ticker` only when `symbol` is truthy; drop the dead `instrument_symbol` second-chance lookup (`_view_run_for_resolver` always supplies `symbol`).
- [x] [Review][Patch] POST success HX-Redirect validates `explorer_return` through `_safe_explorer_return` before appending to the redirect URL. Invalid values (javascript:, //evil, control chars) are dropped; the user still lands on `/backtests/{run_id}`.
- [x] [Review][Patch] `_build_run_backtest_url` emits `explorer_return` only when at least one explorer-state value is truthy — `any(v not in (None, "") for v in explorer_state.values())`. Default-filter anchors no longer carry a redundant duplicate of catalog+ticker+tf.
- [x] [Review][Patch] Cap `explorer_return` length at `_EXPLORER_RETURN_MAX_LEN = 2000` in both GET (`_build_run_context`) and POST (form ingestion). Oversized values fall back to deterministic reconstruction on the detail page.
- [x] [Review][Patch] Tightened `test_button_focus_ring` — now uses `mock_metadata_service.get_instrument.return_value` and scopes the ring-class assertion to the markup window of the Run Backtest anchor (parsed via `<a ` search anchored on `aria-label="Run backtest for AAPL"`).
- [x] [Review][Patch] Tightened `test_button_threads_explorer_state` — extracts the anchor's `href=`, URL-decodes the nested `explorer_return`, and asserts each state field (`search=A`, `asset_class=STOCK`, `sort_by=ticker`, `page=2`) appears inside.
- [x] [Review][Patch] Renamed `_TIMEFRAME_EXPLORER_TO_RUN_FORM` and `_TIMEFRAME_RUN_FORM_TO_EXPLORER` to public names (dropped the leading underscore) and updated the cross-module import in `src/api/ui/backtests.py` + the unit-test import in `tests/unit/api/test_explorer_bridge_url.py`.

**Deferred** — pre-existing or out-of-scope:

- [x] [Review][Defer] Explorer state params (`search`, `asset_class`, `sort_by`, `page`) flow into the bridge URL without allow-list validation or length caps [src/api/ui/explorer.py `chart_panel_fragment`] — pre-existing Epic 2 concern; Story 3.2 just threads them through unchanged.
- [x] [Review][Defer] `date_range_start/end.strftime("%Y-%m-%d")` has no timezone normalization; a tz-naive legacy row would produce a date in the DB's implicit zone rather than UTC [src/api/ui/explorer.py `_build_run_backtest_url`] — `CatalogInstrument.date_range_*` columns are `TIMESTAMP(timezone=True)` in practice; low-risk.
- [x] [Review][Defer] `data_source.split(":", 1)[1]` assumes no colon inside catalog names [src/api/ui/backtests.py:590-592] — catalog-name validator rejects non-`[A-Za-z0-9_-]` at import time, so colons cannot reach this branch. Theoretical only.
- [x] [Review][Defer] `bar_type[:-len("-LAST")]` assumes the `-LAST` aggregation suffix always applies [src/api/ui/backtests.py:594] — all current bar types are LAST-aggregated; BID/ASK/MID aggregations are future concerns.
- [x] [Review][Defer] Fallback "Back to Explorer" URL does not preserve `search`/`asset_class`/`sort_by`/`page` when `explorer_return` is absent [src/api/ui/backtests.py:157-162] — inherent limitation of the deterministic fallback; acceptable per spec.
- [x] [Review][Defer] `_BRIDGE_PARAM_MAP` relies on `form_data=raw_data` being used on the validation-error re-render path; a future refactor to `form_data=form.model_dump()` would silently drop `explorer_return` because it isn't a field on `BacktestRunFormData` [src/api/ui/backtests.py `_build_run_context`] — latent footgun; spec explicitly chose to keep `explorer_return` out of the form model. Document or add a regression test later.

_Dismissed (12): KeyError on unmapped explorer timeframe (intentional per spec), `test_button_absent_when_chart_panel_not_loaded` (actually proves AC #2 correctly), `test_button_url_encodes_special_ticker` naming quibble (`.` requires no encoding, the test proves ticker survives), `page=0` dead defensive branches, unvalidated `explorer_return` in form re-render (subsumed by the POST-redirect patch), app-wide CSRF (out of scope), `urlencode` idiom preference, XSS test already co-asserts `1-DAY` default, catalog blanking lost on POST (POST has no query string, invalid claim), raw-vs-decoded log value (observability nicety), double-decode-via-log-sink speculation, speculative future refactor hazards._

## Dev Notes

### Architecture Compliance

- **HTMX vs. full-page navigation decision.** The Run Backtest button is a full-page navigation (`<a href>`), NOT an HTMX swap. Rationale: (a) the backtest run page is a `base.html`-extending page with its own nav/breadcrumbs; an `hx-get` into `#explorer-content` would attempt to swap a full `<html>` document into a fragment wrapper and break the layout. (b) Middle-click / ⌘-click to open in a new tab is a critical power-user flow — HTMX would hijack that. (c) The backtest run is a distinct task surface per the UX spec "Journey 3: Explorer to Backtest"; a full page transition reinforces the mental model. See `_bmad-output/planning-artifacts/ux-design-specification.md:500-526`.
- **State-in-URL principle.** Every bridge query param is serializable as a bookmarkable URL — this is a deliberate extension of the Epic 2 state-in-URL contract (see UX spec "Deep linking" § line 797-799). No session cookies, no server-side session state for `explorer_return`. This keeps the pattern uniform.
- **Open-redirect defense posture.** `explorer_return` is user-controlled input that ends up in an `href`. Treat it the same way as any `?next=` parameter in an auth flow: strict prefix allow-listing, URL-decode before check, reject protocol-relative URLs. See [OWASP Unvalidated Redirects](https://owasp.org/www-community/attacks/Unvalidated_Redirects_and_Forwards). Test matrix lives in Task 5.3.
- **XSS autoescape.** All four new bridge query params hit Jinja2 templates via `form_data.get("x", "")` — Jinja2 autoescape (enabled by default in `Jinja2Templates`) covers the rendering path. Do NOT use `| safe` filter on any of them. Test 3.2 (Task 3.2) asserts `<script>` escapes to `&lt;script&gt;`.
- **BacktestRequest boundaries (unchanged).** Story 3.2 touches no service-layer code. All Story 3.1 invariants — `catalog_name` validator, `to_persistence_data_source()`, multi-instrument `execute_multi` — remain exactly as landed in commit `618836e`. No edits to `src/models/backtest_request.py`, `src/services/firstrate/backtest_loader.py`, `src/core/backtest_orchestrator.py`.

### Critical Implementation Details

**Timeframe mapping (single source of truth):**

```python
# src/api/ui/explorer.py (additions)
_TIMEFRAME_EXPLORER_TO_RUN_FORM: Final[dict[str, str]] = {
    "D": "1-DAY",
    "1H": "1-HOUR",
    "5m": "5-MINUTE",
    "1m": "1-MINUTE",
}

# Reverse for the detail-page fallback.
_TIMEFRAME_RUN_FORM_TO_EXPLORER: Final[dict[str, str]] = {
    v: k for k, v in _TIMEFRAME_EXPLORER_TO_RUN_FORM.items()
}
```

These four entries match exactly `ExplorerTimeframe` — the explorer has no `4-HOUR` or `1-WEEK`, so the run form's extra timeframes are not reachable through the bridge but remain available for direct-URL `/backtests/run` use.

**URL construction:**

```python
from urllib.parse import urlencode, quote

def _build_run_backtest_url(
    *,
    catalog: str,
    ticker: str,
    active_tf: ExplorerTimeframe,
    date_range_start: datetime | None,
    date_range_end: datetime | None,
    bar_count: int,
    explorer_state: dict[str, Any] | None = None,
) -> str | None:
    """Build the /backtests/run bridge URL; returns None when button should be hidden."""
    if bar_count <= 0:
        return None
    try:
        run_form_tf = _TIMEFRAME_EXPLORER_TO_RUN_FORM[active_tf.label]
    except KeyError:
        # New explorer timeframe without a run-form mapping — loud failure.
        raise KeyError(f"No run-form timeframe for {active_tf.label!r}") from None

    params: list[tuple[str, str]] = [
        ("catalog", catalog),
        ("ticker", ticker),
        ("timeframe", run_form_tf),
    ]
    if date_range_start is not None:
        params.append(("start", date_range_start.strftime("%Y-%m-%d")))
    if date_range_end is not None:
        params.append(("end", date_range_end.strftime("%Y-%m-%d")))

    if explorer_state:
        explorer_url = _build_explorer_return(catalog, ticker, active_tf, explorer_state)
        params.append(("explorer_return", explorer_url))

    return f"/backtests/run?{urlencode(params)}"


def _build_explorer_return(
    catalog: str, ticker: str, active_tf: ExplorerTimeframe, state: dict[str, Any]
) -> str:
    """Build the /explorer?... URL that 'Back to Explorer' will navigate to."""
    inner: list[tuple[str, str]] = [
        ("catalog", catalog),
        ("ticker", ticker),
        ("tf", active_tf.label),
    ]
    for key in ("search", "asset_class", "sort_by", "page"):
        value = state.get(key)
        if value not in (None, "", 0, "0"):
            inner.append((key, str(value)))
    return f"/explorer?{urlencode(inner)}"
```

Note: `urlencode` percent-encodes the `&` and `=` inside `explorer_return`'s value automatically, so the outer URL is safe.

**Run-form pre-fill (extend `_build_run_context`):**

```python
# src/api/ui/backtests.py (additions to _build_run_context)
merged_form = dict(form_data or {})
qp = request.query_params

# Story 3.1 (unchanged):
catalog_from_query = qp.get("catalog")
if catalog_from_query and not merged_form.get("catalog_name"):
    merged_form["catalog_name"] = catalog_from_query

# Story 3.2 additions — single-pass, defensive:
_BRIDGE_PARAM_MAP = {
    "ticker": "symbol",
    "start": "start_date",
    "end": "end_date",
    "timeframe": "timeframe",
}
for src_key, form_key in _BRIDGE_PARAM_MAP.items():
    value = qp.get(src_key)
    if value and not merged_form.get(form_key):
        if form_key == "timeframe" and value not in VALID_TIMEFRAMES:
            continue  # silently drop unknown timeframes (AC #7)
        merged_form[form_key] = value

# explorer_return (for POST round-trip):
explorer_return = qp.get("explorer_return")
if explorer_return and not merged_form.get("explorer_return"):
    merged_form["explorer_return"] = explorer_return
```

**"Only pre-fill when empty" guard.** The `and not merged_form.get(form_key)` clauses preserve the existing pattern from Story 3.1's `catalog_from_query` logic — a re-rendered form after a validation error should NOT have its user-entered values overwritten by the URL hint. This is load-bearing for AC #15 (no regressions).

**Open-redirect guard on `explorer_return`:**

```python
import structlog
from urllib.parse import unquote

logger = structlog.get_logger(__name__)

def _resolve_back_to_explorer_url(
    explorer_return: str | None, run: Any
) -> str:
    """Validate explorer_return; fall back to deterministic reconstruction on any failure."""
    if explorer_return:
        decoded = unquote(explorer_return).strip()
        if decoded.startswith("/explorer") and not decoded.startswith("//"):
            # Must be either exactly "/explorer" or "/explorer?<query>"
            if decoded == "/explorer" or decoded.startswith("/explorer?"):
                return decoded
        logger.warning(
            "explorer_return_rejected", value=explorer_return, reason="prefix_mismatch"
        )
    # Deterministic fallback
    if not run.catalog_name:
        return "/explorer"
    parts: list[tuple[str, str]] = [("catalog", run.catalog_name), ("ticker", run.symbol)]
    tf_label = _TIMEFRAME_RUN_FORM_TO_EXPLORER.get(run.timeframe)
    if tf_label:
        parts.append(("tf", tf_label))
    return f"/explorer?{urlencode(parts)}"
```

The `not decoded.startswith("//")` check rejects protocol-relative URLs like `//evil.example.com` which a naive `startswith("/explorer")` would miss.

**`explorer_return` on success redirect (Task 4):**

```python
# src/api/ui/backtests.py — success branch (around line 234-237)
response = Response(status_code=200)
redirect_url = f"/backtests/{run_id}"
explorer_return = raw_data.get("explorer_return") or form.get("explorer_return")
if explorer_return:
    redirect_url += "?" + urlencode([("explorer_return", explorer_return)])
response.headers["HX-Redirect"] = redirect_url
return response
```

Use `urlencode` — not string concatenation — so `explorer_return` containing `&` survives the round-trip.

**Chart-panel template surface (minimal patch):**

Around `templates/explorer/chart_panel.html:32-35`:

```html
<div class="ml-auto flex items-center gap-3">
    <span class="text-xs text-slate-500">{{ ticker }} &middot; {{ bar_count }} bars</span>
    {% if run_backtest_url %}
    <a href="{{ run_backtest_url }}"
       class="bg-blue-500 hover:bg-blue-600 text-white text-sm font-medium px-3 py-1 rounded focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-950 focus:outline-none"
       aria-label="Run backtest for {{ ticker }}">
        Run Backtest
    </a>
    {% endif %}
</div>
```

**Why `<a>` not `<button>`:** anchor with `href` supports middle-click, ⌘-click, right-click → "Open in new tab". `<button>` would need a JS handler for any of those and is semantically wrong for same-origin navigation.

### Existing Code to Reuse (DO NOT duplicate)

| What | Where | How to use |
|------|-------|------------|
| `ExplorerTimeframe` enum + `.label` + `.bar_type_spec` | `src/api/models/explorer.py:18-66` | Source of the four explorer timeframes. Map `.label` → run-form timeframe via the new module constant. |
| `VALID_TIMEFRAMES` tuple | `src/api/models/run_backtest.py:14-22` | Allow-list for the incoming `timeframe` query param (AC #7). |
| `_build_run_context(request, ...)` | `src/api/ui/backtests.py:74-107` | Extend the catalog-prefill branch — do NOT fork a new context builder. |
| `BacktestRunFormData` | `src/api/models/run_backtest.py:33-66` | Already accepts `catalog_name: str \| None` per Story 3.1. Do NOT add `explorer_return` here — it's session state, not domain state. |
| HX-Redirect success pattern | `src/api/ui/backtests.py:234-237` | Extend the redirect URL construction; keep the lock semantics. |
| `Jinja2Templates` autoescape | `src/api/ui/explorer.py:39`, `src/api/ui/backtests.py:43` | Default ON — all new template interpolations use `{{ x }}` not `{{ x \| safe }}`. |
| `structlog.get_logger(__name__)` | Existing use in `explorer.py:36` and `backtests.py:40` | Use for the `explorer_return_rejected` WARNING log. |
| Backtest detail view factory (`to_detail_view`) | `src/api/models/backtest_detail.py` | Extend to carry `back_to_explorer_url` — keep logic in a pure resolver called from the route. |
| `CatalogInstrument.date_range_start / date_range_end` | `src/db/models/catalog_instrument.py` | Already in `_build_ticker_stats` / `_stats_template_context` — already threaded into `chart_panel.html` via `stats_template_context`. Reuse — no new DB queries. |
| `stats_template_context` dict | `src/api/ui/explorer.py:46-65` | `chart_panel.html` already has `date_range_start` and `date_range_end` in its context. |
| `urllib.parse.urlencode` / `unquote` / `quote` | Standard library | Single canonical encoder — no manual string interpolation. |
| Jinja `tojson` filter | Used in `chart_panel.html:23` and `explorer.html:85` | For safely embedding strings into JS — not needed for `<a href>`, but keep the pattern if JS ever touches these values. |

### File Structure

**New files:**

- `tests/unit/api/test_explorer_bridge_url.py` — unit tests for `_build_run_backtest_url`, `_build_explorer_return`, `_resolve_back_to_explorer_url`, and the two timeframe-mapping constants. Pure functions, no I/O, no DB.
- (Optional) `tests/component/api/test_backtest_detail_routes.py` — if it doesn't exist, create it for the "Back to Explorer" link tests. If it DOES exist (check first — the project has `tests/ui/test_backtest_detail.py` but that's at a different tier), extend rather than duplicate. Check `tests/component/api/` for any existing detail-route tests before adding.

**Modified files:**

- `src/api/ui/explorer.py` — add `_TIMEFRAME_EXPLORER_TO_RUN_FORM`, `_TIMEFRAME_RUN_FORM_TO_EXPLORER`, `_build_run_backtest_url`, `_build_explorer_return` helpers. Extend `chart_panel_fragment` to accept optional `search` / `asset_class` / `sort_by` / `page` query params and thread them. Pass `run_backtest_url` to the template context.
- `src/api/ui/backtests.py` — extend `_build_run_context` to consume the bridge query params + `explorer_return`. Extend the success HX-Redirect to append `explorer_return`. Add `_resolve_back_to_explorer_url` helper. Thread `back_to_explorer_url` into the detail-page context.
- `src/api/models/backtest_detail.py` — if the detail view model uses a factory (`to_detail_view(run, ...)`), add a `back_to_explorer_url` field and parameter. Keep the resolver call OUTSIDE the model (pure data class), route supplies the computed URL.
- `templates/explorer/chart_panel.html` — add the Run Backtest anchor to the timeframe-toolbar right-hand side (gated on `run_backtest_url`).
- `templates/explorer/explorer.html` (lines ~83-85) — extend `hx-vals` on the `#chart-panel` HTMX trigger to pass `search`, `asset_class`, `sort_by`, `page` from `state.*`.
- `templates/backtests/run.html` — add hidden `explorer_return` input inside the form.
- `templates/backtests/detail.html` — add the "Back to Explorer" link as the leftmost action button in the action row (around line 60).
- `tests/component/api/test_chart_panel_routes.py` — new `TestRunBacktestButton` class with the three tests from Task 1.
- `tests/component/api/test_run_backtest_routes.py` — new `TestBridgePreFill` class with the three tests from Task 3.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — status transition (automated by the workflow).

**No new migrations, no new REST endpoints, no new Pydantic response models, no new service-layer code.**

### Project Structure Notes

- `src/api/ui/explorer.py` owns the bridge URL construction because it's where the chart-panel render happens. The `_build_run_backtest_url` helper is scoped to this module — do NOT promote it to `src/api/models/explorer.py` (which is for data shapes, not URL factories) or `src/core/` (not a domain concept).
- The reverse timeframe map (`_TIMEFRAME_RUN_FORM_TO_EXPLORER`) is imported from `src/api/ui/explorer.py` into `src/api/ui/backtests.py` as-needed. That's a valid sibling import — both are UI-layer modules with no direction bias.
- `_resolve_back_to_explorer_url` belongs in `src/api/ui/backtests.py` because it's tied to the run-detail response shape. Keep it a private module-level function, not a method on any model.

### Testing Requirements

- **TDD non-negotiable.** Every task's first subtask is a failing test.
- **Test pyramid distribution:**
  - **Unit** (`make test-unit`): the three URL-builder helpers + both timeframe-mapping constants. Pure functions, no Jinja, no TestClient. Under `tests/unit/api/test_explorer_bridge_url.py`.
  - **Component** (`make test-component`): chart-panel anchor rendering + run-form pre-fill + HX-Redirect with `explorer_return` + detail-page "Back to Explorer". Uses `TestClient` + dependency-override fixture shape from `test_stats_panel_routes.py`. No real DB, no real Parquet.
  - **Integration** (`make test-integration`): NONE. This story doesn't touch the engine, loader, catalog, or any Nautilus C extension. No `--forked` tests.
  - **UI / E2E** (agent-browser, Task 7): full bridge flow from explorer click to "Back to Explorer" click. Manual capture to `/tmp/story-3-2-evidence/`.
- **Regression baselines (from Story 3-1):** 776 unit + 586 component + 16 component skips (pre-existing). Integration has ~15 pre-existing flakes — do NOT over-state pass/fail; run and compare.
- **XSS test is mandatory** (Task 3.2) — `<script>` in bridge query params must escape. One of the two things a security review will check.
- **Open-redirect test matrix is mandatory** (Task 5.3) — parametrised over `https://`, `//`, `javascript:`, `data:`, `../` (path traversal). The other thing a security review will check.
- **No new fixtures.** Every new test reuses the existing `TestClient` + dependency-override shape from `tests/component/api/test_stats_panel_routes.py` / `test_run_backtest_routes.py`. Adding a new fixture file is a code-review red flag for this story.

### Previous Story Intelligence

**From Story 3-1 (catalog integration with BacktestEngine) — directly load-bearing:**

- **`catalog_name` pre-fill from `?catalog=` already exists** (`src/api/ui/backtests.py:91-97` via `catalog_from_query`). Story 3.2 extends this exact pattern with four additional query params — do NOT refactor `_build_run_context` into a generic query-param consumer that breaks the existing Story 3.1 guard ("only pre-fill when form_data is empty"). The guard is specifically there for the validation-error re-render path.
- **`BacktestRunFormData.catalog_name` is already validated** per Story 3.1's Review Patches (whitespace → None, `^[A-Za-z0-9_-]+$`, max_length=64). Story 3.2 does NOT duplicate validation on the GET side — the POST validator is the gatekeeper. An invalid-on-GET catalog name renders in the readonly input and fails cleanly on submit (AC #8).
- **The readonly `catalog_name` block in `run.html:142-150` reveals only when set** via `{% if not _catalog_value %} class="hidden"{% endif %}`. Story 3.2 doesn't change this — the bridge just populates the value; the reveal logic is reused verbatim.
- **`BacktestRequest.catalog_name` validator rejects `catalog_name` set when `data_source != "catalog"`** (Story 3.1 model_validator). The bridge always sets `data_source=catalog` implicitly via the form default; this invariant is not disturbed.
- **R11 (deferred-work.md):** `execute_multi` has no user-facing input channel. Story 3.2 is the correct place to add one, but it's explicitly OUT OF SCOPE here (see Scope & Non-Goals). The bridge carries exactly one ticker. Multi-ticker selection is a Story 3.3+ / Phase 2 concern.

**From Story 2-4 (explorer UX polish) — carry forward:**

- **Focus-ring pattern:** Story 2-4 established `focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-950` for all interactive elements. Both the "Run Backtest" anchor AND the "Back to Explorer" link must use this pattern (AC #14).
- **ARIA labels on interactive elements:** Story 2-4 pattern requires `aria-label` on all non-text interactive elements. The Run Backtest anchor has text ("Run Backtest") but MUST still include `aria-label="Run backtest for {ticker}"` — the ticker context is not in the visible text, only in the aria-label (matches `aria-label="Price chart for {ticker}"` pattern in `chart_panel.html:58`).
- **HTMX error handler in `templates/partials/htmx_error_handler.html`** — this story adds no new HTMX swaps, so no new error-handler paths. If a test inadvertently triggers the HTMX handler (e.g., via a `hx-get` on the new anchor), you've chosen the wrong element; anchors are plain `<a href>`.
- **U1 / U2 (deferred):** concurrent HTMX races on the same target. Story 3.2 adds zero new HTMX paths — U1/U2 surface area does not grow.

**From Story 2-3 (data statistics panel) — relevant for chart-panel context plumbing:**

- **Ruff auto-linter strips "unused" imports.** Bundle the import of `urlencode`/`quote`/`unquote` with their first usage in a single edit. If the linter flags anything, re-read the file and bundle in one edit. The `noqa: F401` escape hatch exists for genuinely-forward-declared imports (see `src/api/ui/explorer.py:9-34` for the existing no-qa pattern).
- **`_build_ticker_stats` is reused, not duplicated, in chart_panel_fragment.** Follow the same shared-helper pattern for `_build_run_backtest_url`.

**From Epic 2 retro — Epic 3 prep items already landed:**

- **B2 (sync-in-async) — DONE.** Bridge path is synchronous Jinja rendering; no `asyncio.to_thread` concerns.
- **P1 (AAPL 2018 reference dataset) — CONFIRMED.** Story 3.3 (next) uses AAPL 2018 1-MINUTE `AAPL.NASDAQ`. Story 3.2 does NOT pre-commit a ticker — the bridge works for any imported ticker.
- **P4 (compiled-CSS smoke) — NOT DONE.** If the button's Tailwind classes (`bg-blue-500`, `hover:bg-blue-600`, `focus:ring-offset-slate-950`) don't end up in `app.css`, the button renders without styling. Retro P4 would have caught this as a generic guard; for this story, the `agent-browser` screenshot in Task 7.3 is the only verification. Rebuild CSS if anything looks off.
- **P5 (CLAUDE.md ruff docs) — partially addressed, not yet landed.** Known trap; bundle edits.

### Git Intelligence (last 5 commits)

- `618836e feat(backtest): catalog integration with BacktestEngine (Story 3-1)` — direct predecessor. Established the `catalog_name` pre-fill on `?catalog=` query param and the readonly `catalog_name` input. Story 3.2 extends both. The commit's Review Patches include the `catalog_name` validator (whitespace + charset + max_length) — Story 3.2 depends on these being in place for AC #8.
- `10d8ba6 feat(retro): Epic 2 retrospective and Epic 3 blocker fixes` — resolved B2 (sync-in-async). Story 3.2 does not add new async/sync bridges.
- `56a7a73 fix(explorer): Story 2-4 code review patches` — XSS + retry GET-only guardrails. The chart-panel template patches landed here; Story 3.2 rides the same autoescape posture.
- `31b126a feat(explorer): UX polish pass (Story 2-4)` — established the focus-ring / aria-label / retry handler patterns reused here.
- `c88a025 fix(quality): resolve lint, type, and env-leakage test failures` — confirms `make format && make lint && make typecheck` is the quality-gate baseline Task 8.1 must maintain.

### Known Constraints / Carryover Debt

- **R2 (Story 3-1 deferred):** Load-phase not covered by `timeout_seconds`. Bridge-initiated runs hit this same slow-read path — a large-window AAPL-2018-2024 1-MINUTE backtest launched from the explorer button can hold `_backtest_lock` for the Parquet read duration. Story 3.2 does NOT fix R2; the bridge UX just makes it easier to trigger it. Note in release notes if the first bridge-launched run feels laggy.
- **R3 (Story 3-1 deferred):** `data_source="catalog:<name>"` persisted values would fail `BacktestRequest.data_source` validator on reconstruction. Story 3.2 does NOT reconstruct from DB rows — still safe, but "Back to Explorer" from a persisted run uses `run.catalog_name` (stored separately) not `data_source`.
- **R11 (Story 3-1 deferred):** `execute_multi` user-facing input channel. **Explicitly out of scope for 3.2.** Bridge is single-ticker only. If Story 3.3 surfaces multi-ticker needs, a follow-up story adds a list-of-symbols input.
- **U1, U2 (Story 2-4 deferred):** HTMX races. Bridge is zero-HTMX, so no new exposure.
- **D11 (Epic 1 carryover):** silent reimport on regressed source. Bridge URL uses `date_range_start/end` from `CatalogInstrument` metadata — if metadata is stale (D11 scenario), the bridge passes a wider date range than the actual Parquet data covers. Story 3-1's empty-window error surfaces this with both `metadata_range` and request range in the message. No new handling required in 3.2 — the existing error flow is the diagnostic path.
- **Timeframe coverage:** the run form has 7 timeframes, the explorer has 4. Bridge only covers the 4 overlapping ones. If the product grows explorer to cover 15m/4H/1W, the mapping constants must be extended — `KeyError` at render time ensures loud failure rather than silent fallback.
- **`explorer_return` URL length:** a fully-decorated explorer state (`catalog + ticker + tf + search + asset_class + sort_by + page`) encoded into `explorer_return` which itself is encoded into the bridge URL can approach ~300 bytes. Browsers accept URLs up to ~2000 chars safely; pathological user input (e.g., a 500-char search query) would push close. Out of scope: input-length caps on `explorer_return`. If the cap is needed, enforce on the explorer-state capture side.

### Anti-patterns to Avoid

- **Using `<button onclick="location='...'">` instead of `<a href>`** — breaks middle-click / ⌘-click / right-click → "Open in new tab". The anchor is the correct element.
- **Using `hx-get="/backtests/run"` on the button** — the backtest run page is a full-page template extending `base.html`; an HTMX swap would inject the full `<html>…</html>` into the `#explorer-content` wrapper and collapse the layout.
- **Adding a new `data_source` value for "bridge-initiated"** — the `data_source` allow-list is `{catalog, ibkr, kraken, mock}` per Story 3.1 and must not churn. Bridge-initiated runs look identical to any other `data_source=catalog` run on the persistence side.
- **Pre-selecting a strategy in the run form** — AC #6 explicitly does not do this. The bridge supplies DATA context, not STRATEGY choice.
- **Server-side session state for `explorer_return`** — use URL params only. No cookies, no Redis, no in-memory cache.
- **Client-side JS for URL construction** (`document.location.href` etc.) — break SSR, break noscript, break `<a href>` preview on hover. Server renders the URL.
- **Generic `next=` query param name** — explicit is better: `explorer_return` signals intent and narrows the open-redirect threat model (vs. a universal `?next=` that every future flow wants to piggyback on).
- **Forking `_build_run_context`** into `_build_run_context_from_bridge` — extend the existing function; the "only pre-fill when empty" guard must remain uniform across all URL-sourced fields.
- **Adding `explorer_return` to `BacktestRunFormData`** — it's session state, not domain state. Staying out of the Pydantic model keeps `BacktestRequest`, persistence, and the backtest run row clean.
- **Silently fallback on unmapped explorer timeframe** — a dict-get-with-default would hide a future bug when someone adds a fifth explorer timeframe. Use explicit `KeyError`.
- **Over-engineering "Back to Explorer"** — do NOT persist explorer state into the database, a cookie, or the backtest_runs table. A URL round-trip is sufficient; the fallback-from-run-context path (AC #11) is the long-term acceptable degradation.
- **Omitting the open-redirect guard** — every `?explorer_return=` value is user-controlled input that lands in an `href`. Missing the `startswith("/explorer")` + `not startswith("//")` check is a security issue, not a polish concern.
- **Letting `make test-integration` failure count grow** — Story 3.1 baseline was 15 pre-existing flakes. No new integration tests; the count must not increase. If it does, Story 3.2 has a regression.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md#Story 3.2 Explorer-to-Backtest Bridge` (lines 625-652)] — canonical acceptance criteria.
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md#Journey 3: Explorer to Backtest` (lines 500-526)] — user journey, key UX moments, "Run Backtest" as primary action.
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md#Navigation Patterns` (lines 780-799)] — deep-linking contract; every state is a bookmarkable URL; "Back to Explorer preserves the last explorer state".
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md#Button Hierarchy` (lines 822-826)] — `bg-blue-500 text-white` for primary action, one per context; "Run Backtest" is the explorer's primary action.
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md#Implementation Approach` (lines 409-430)] — "Run Backtest link navigates to existing backtest run page with query params: ?catalog=...&ticker=...&timeframe=...&start=...&end=..." — the canonical URL shape this story implements.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Explorer Boundary` (lines 511-527)] — UI route (`explorer.py`) composes templates; REST routes produce JSON. Bridge is UI-layer, not REST.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Cross-Cutting Integration Points` (lines 569-585)] — `CatalogManager` as the central integration point; `MetadataService` bridges import and explorer.
- [Source: `_bmad-output/implementation-artifacts/3-1-catalog-integration-with-backtestengine.md#Task 6` (lines 93-99)] — the exact predecessor: `?catalog=` pre-fill, readonly field, hidden-when-empty. Story 3.2 extends this pattern.
- [Source: `_bmad-output/implementation-artifacts/2-4-explorer-ux-polish.md`] — focus-ring + aria-label patterns.
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md#R1-R11`] — Story 3-1 deferreds relevant to bridge.
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md#U1-U10`] — Story 2-4 deferreds (HTMX races, focus-ring). Bridge adds no new HTMX surface.
- [Source: `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-19.md#5 Epic 3 Preview & Dependencies` (lines 82-107)] — "Story 3.2's bridge can build `?catalog=…&ticker=…&timeframe=…&start=…&end=…` from `selected_catalog`, `selected_ticker`, `selected_tf`, and `instrument.date_range_{start,end}` without any explorer refactor." Direct alignment with this story's implementation.
- [Source: `src/api/ui/explorer.py:300-397`] — `chart_panel_fragment` to extend; already has `instrument.date_range_start/end` via `_build_ticker_stats`.
- [Source: `src/api/models/explorer.py:18-66`] — `ExplorerTimeframe`: `D / 1H / 5m / 1m` labels. Complete set.
- [Source: `src/api/models/run_backtest.py:14-22`] — `VALID_TIMEFRAMES`: `{1-MINUTE, 5-MINUTE, 15-MINUTE, 1-HOUR, 4-HOUR, 1-DAY, 1-WEEK}`. Mapping target.
- [Source: `src/api/ui/backtests.py:74-107`] — `_build_run_context` to extend with four more bridge query params + `explorer_return`.
- [Source: `src/api/ui/backtests.py:234-237`] — HX-Redirect success branch to extend with `?explorer_return=` append.
- [Source: `templates/explorer/chart_panel.html:7-35`] — timeframe toolbar. The "Run Backtest" anchor sits beside the ticker/bar-count span (right-aligned, sibling of the `role="toolbar"` wrapper).
- [Source: `templates/explorer/explorer.html:79-99`] — `#chart-panel` HTMX trigger; extend `hx-vals` with `search`, `asset_class`, `sort_by`, `page` from `state.*`.
- [Source: `templates/backtests/run.html:140-150`] — readonly `catalog_name` block from Story 3.1. Hidden `explorer_return` field goes nearby.
- [Source: `templates/backtests/detail.html:59-97`] — action-button row. "Back to Explorer" goes as the leftmost action.
- [Source: `CLAUDE.md#Editing with Auto-Linter` (Epic 2 retro P5)] — bundle imports + usage in a single edit to survive ruff's F401 strip pass.
- [Source: `CLAUDE.md#UI Testing (agent-browser)`] — `agent-browser snapshot -i` for element refs, re-snapshot after navigation, full dev-server pattern for Task 7.
- [Source: OWASP Unvalidated Redirects & Forwards] — `explorer_return` guard rationale; the pattern is standard.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7 (Opus 4.7, 1M context)

### Debug Log References

- Ruff auto-linter did not strip any imports during this story (bundled imports with usages in a single edit as required by CLAUDE.md P5).
- Component test for the Run Backtest anchor initially asserted the contiguous string `">Run Backtest<"`, which fails because Jinja renders the anchor text across multiple lines — relaxed to substring `"Run Backtest"` + `aria-label="Run backtest for SPY"` which uniquely identifies the anchor.
- Hidden `explorer_return` field rendered only when truthy (cleaner than always-present empty value); test relaxed to match either shape but we picked the truthy-guarded form in `run.html`.

### Completion Notes List

- All 15 acceptance criteria satisfied by 35 new unit tests + 22 new component tests + curl-based smoke verification of the full bridge round-trip.
- `_build_run_backtest_url` returns `None` only for zero-bar instruments (AC #5) — missing date range metadata still produces a URL, just without `start=`/`end=` params.
- Open-redirect guard (AC #12) validates prefix `/explorer` + rejects protocol-relative `//` URLs, with structured WARNING log `explorer_return_rejected` for observability.
- `_view_run_for_resolver` adapts BacktestRun ORM rows to the flat fields the resolver needs: extracts `catalog_name` from either `config_snapshot` or the `catalog:<name>` prefix of `data_source` (Story 3.1 persistence format), and strips `-LAST` from `bar_type` to recover the run-form timeframe shape.
- `explorer_return` kept out of `BacktestRunFormData` — it's session state, not domain state. Lives in `raw_data` only and rides through form re-renders via `form_data=raw_data`.
- Explorer state threaded through via route query params (`search`, `asset_class`, `sort_by`, `page`) rather than by modifying existing data models — minimal surface change. `hx-vals` on both the chart-panel trigger AND the timeframe buttons now carry this state to preserve the round-trip URL across tf switches.
- No new REST endpoints, no new Pydantic models, no new service-layer code, no DB migrations — pure template/routing plumbing as intended.

### File List

**New files:**
- `tests/unit/api/test_explorer_bridge_url.py` — 35 tests across timeframe mapping, `_build_run_backtest_url`, `_build_explorer_return`, `_resolve_back_to_explorer_url`.
- `tests/component/api/test_backtest_detail_routes.py` — 9 tests for "Back to Explorer" link + open-redirect guard.

**Modified files:**
- `src/api/ui/explorer.py` — added `_TIMEFRAME_EXPLORER_TO_RUN_FORM`, `_TIMEFRAME_RUN_FORM_TO_EXPLORER`, `_build_explorer_return`, `_build_run_backtest_url`; extended `chart_panel_fragment` with optional `search`/`asset_class`/`sort_by`/`page` query params; threaded `run_backtest_url` + `explorer_state_*` into template context.
- `src/api/ui/backtests.py` — imported `_TIMEFRAME_RUN_FORM_TO_EXPLORER`; added `_BRIDGE_PARAM_MAP` + bridge pre-fill logic to `_build_run_context`; added `_resolve_back_to_explorer_url` + `_view_run_for_resolver`; extended `run_backtest_submit` to capture `explorer_return` from form into `raw_data` and append it to the success `HX-Redirect`; extended `backtest_detail` to compute + pass `back_to_explorer_url`.
- `templates/explorer/chart_panel.html` — added Run Backtest `<a>` anchor inside the timeframe toolbar's right-hand wrapper, gated on `{% if run_backtest_url %}`; extended timeframe-button `hx-vals` to preserve explorer state across tf switches.
- `templates/explorer/explorer.html` — extended chart-panel trigger `hx-vals` to include `search`/`asset_class`/`sort_by`/`page` from `state.*`.
- `templates/backtests/run.html` — added hidden `explorer_return` input gated on truthy value.
- `templates/backtests/detail.html` — added "Back to Explorer" link as the leftmost action button.
- `tests/component/api/test_chart_panel_routes.py` — added `TestRunBacktestButton` class (7 tests).
- `tests/component/api/test_run_backtest_routes.py` — added `TestBridgePreFill` class (4 tests) + 2 redirect tests in `TestPostRunBacktest`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — status transitions ready-for-dev → in-progress → review.

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-04-19 | Amelia (dev agent) | Implemented Story 3.2 Explorer-to-Backtest Bridge. 57 new tests across unit + component tiers. All 15 ACs satisfied. Status → review. |
| 2026-04-19 | Bob (SM) | Story 3.2 created with comprehensive developer context — explorer-to-backtest bridge: chart-panel "Run Backtest" anchor, run-form pre-fill across four bridge params, `explorer_return` round-trip for "Back to Explorer" link with open-redirect guard. Status: backlog → ready-for-dev. |
