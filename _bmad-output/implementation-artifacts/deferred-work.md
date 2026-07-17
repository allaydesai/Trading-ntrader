# Deferred Work

Real, non-blocking findings deferred from code reviews. Each entry notes its source and why it was deferred.

## Deferred from: code review of story 4-2-search-and-filter-the-etf-ticker-list-by-symbol (2026-07-17)

- **Catalog dropdown drops the active asset-class filter on catalog switch**
  [templates/explorer/explorer.html:38]. The catalog `<select>` uses
  `hx-include="#search-input, [name='asset_class']:checked"`. The only element named `asset_class` is the
  `type="hidden"` input at `templates/explorer/ticker_list.html:10`, which can never match a `:checked` selector
  (the asset-class pills are `<button>` elements, not checkable inputs). So switching catalog forwards **no**
  `asset_class` and the re-rendered list resets to "All" (the "All" pill becomes `aria-pressed="true"`), even though
  the same dropdown *does* preserve the typed `search` via `#search-input`. Pre-existing and **explicitly scoped
  out** of Story 4.2, which fixes only the **search-input** filter-state leak (the story's Dev Notes call catalog-
  switch reset-to-All a defensible fresh-catalog behavior, since a newly-selected catalog may not contain the same
  asset classes). If a future story decides catalog-switch should preserve the pill, the fix mirrors 4.2's:
  replace `[name='asset_class']:checked` with `[name='asset_class']` on the catalog select's `hx-include`. No test
  currently covers the catalog-switch asset_class path.

## Deferred from: code review of story 4-1-browse-imported-etf-tickers (2026-07-16)

- **Stats/detail panel omits the 30min tile** [src/api/models/explorer.py:163-166 (`TickerStatsResponse`);
  templates/explorer/stats_panel.html:19-49]. Story 4.1 added a 30m column to the explorer **browse** row, but the
  ticker **detail/stats** panel still renders only Daily / 1-Hour / 5-Min / 1-Min tiles — `TickerStatsResponse` has
  no `bar_count_30min` field and `stats_panel.html` has no 30-Min tile. So after 4.1, an ETF's browse row shows a
  30m bar count, but clicking through to the stats panel silently drops 30m (browse-vs-detail inconsistency). This
  is the same Story-2.1 "wire 30min by hand everywhere" debt. **Explicitly owned by Story 4.5 (Per-Timeframe ETF
  Data Statistics)**, which is the story that adds per-timeframe stats — Story 4.1's scope boundary deliberately
  left `TickerStatsResponse` untouched, so this is a planned follow-up, not a regression.

- **Cross-run orphan: existing bars + newly-`VENUE_UNRESOLVED` venue nulls `nautilus_id`**
  [src/services/firstrate/instrument_mapper.py:sync_qualification]. When a ticker with bars imported under a
  provisional/CSV venue on a prior run (no-resolver, fault-fallback, or stocks path) later resolves
  `VENUE_UNRESOLVED`, `sync_qualification` writes `nautilus_id = None` / `exchange = None` while the bars remain on
  disk — leaving a `catalog_instruments` row with `bar_count > 0` but a NULL identity, so the bars are unreachable
  by the backtest loader / explorer. (Cache-first resolution means a once-`RESOLVED` ticker stays resolved, so
  this only bites the narrow mixed-path cross-run sequence.)

  **UPDATE (Story 3.5, 2026-07-13): the naive "keep the identity" fix was tried and REJECTED.** Keeping the
  provisional `nautilus_id` so bars stay reachable was implemented in Story 3.5, then reverted during its code
  review: `backtest_loader.load_from_catalog` (and `api/rest/explorer.py`, `api/stats_service.py`,
  `api/chart_bars.py`) gate backtestability **solely on `catalog_instruments.nautilus_id`** — they never consult
  `resolution_status`. So keeping a provisional id lets an unresolved-venue ticker *run a backtest under a guessed
  venue*, directly violating Story 3.5 AC1 ("never silently enter a backtest") and ADR-6 (no guessed venues) —
  strictly worse than the orphan it fixes. Nulling the id is the correct exclusion mechanism the real backtest
  path honors, and the Parquet is never deleted (Story 3.5 AC2 is satisfied: bars physically present, just
  unreachable-by-id until the venue resolves). **The true fix — keep bars reachable AND excluded — needs a
  consumer-honored non-backtestable signal (e.g. a `catalog_instruments.backtestable` flag or a
  `resolution_status` join threaded through `backtest_loader`/explorer/stats/chart), which is a backtest-
  integration change and belongs in Epic 5, not Story 3.5.** Also open: re-resolving to a *different* venue than
  the bars were written under orphans the partition regardless (bar_type path mismatch) — same Epic-5 scope.

## Deferred from: code review of story 2-7-import-summary-and-progress-reporting (2026-06-29)

- **`ResolutionSummary` has no bucket for a non-throwing `UNRESOLVED` record**
  [src/models/instrument_metadata.py:142-147]. `ResolutionSummary.from_results` (Story 1.6) increments
  `resolved` or `venue_unresolved` by status; a clean-degrade `UNRESOLVED` record (distinct from the
  `VENUE_UNRESOLVED` error record) counts toward neither, so `resolved + venue_unresolved` can under-count the
  tickers actually resolved this run, with no "unresolved" line in the summary table. Pre-existing Epic-1
  aggregation semantics — Story 2.7's import-summary wiring only reads `from_results`. Revisit alongside the
  Epic-3 venue worklist / completeness gate, where unresolved-state reporting is in scope.

## Deferred from: code review of story 4-3-chart-a-selected-etf-at-all-5-timeframes (2026-07-16)

- **Stats panel has no 30-Min bar-count card** [src/api/models/explorer.py:161-164;
  src/api/stats_service.py:126-129]. `TickerStatsResponse` and `_build_ticker_stats` carry
  `bar_count_daily/hourly/5min/minute` but not `bar_count_30min`, so the explorer stats panel renders no
  30-Min bar-count card. The 30m *chart* and *toolbar* work correctly (they read
  `CatalogInstrument.bar_count_30min` directly); only the stats *display* omits the 30-min row. Pre-existing
  and explicitly out of Story 4.3's scope ("No stats/metadata-panel work"). Belongs to **Story 4.5**
  (Per-Timeframe ETF Data Statistics), which adds per-timeframe stats incl. 30min.
