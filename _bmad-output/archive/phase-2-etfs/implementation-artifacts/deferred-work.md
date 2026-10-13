# Deferred Work

Real, non-blocking findings deferred from code reviews. Each entry notes its source and why it was deferred.

## Status summary (updated 2026-07-25, venue-gate closure)

**Resolved**

| Item | Where |
|---|---|
| Catalog switch drops the asset-class filter | fixed — `explorer.html` `hx-include`, + a test that pins the selector |
| Stats panel omits the 30min tile (two entries) | closed by Story 4.5; never struck from this file |
| Cross-run orphan / no consumer-honored non-backtestable signal | fixed — see below |
| Trade rows lost for open-position runs | fixed — migrated here from the Story 5.4 file, where it had no tracker home |

**Still open**

| Item | Note |
|---|---|
| `ResolutionSummary` has no bucket for a non-throwing `UNRESOLVED` | unchanged; see entry below |
| `DataCatalogService` rescans `data/bar/` on every request | never tracked here before — added below |
| `test_backtest_catalog_integration.py` is 961 lines | from Story 5.5; added below |

---

## Deferred from: code review of story 4-2-search-and-filter-the-etf-ticker-list-by-symbol (2026-07-17)

**RESOLVED 2026-07-25.** `hx-include` now uses plain attribute selectors
(`[name='asset_class'], [name='sort_by']`), matching the search box and pagination.
The sort column was silently resetting too — `sort_by` was omitted from the
`hx-include` entirely, which the original report did not catch. A test now asserts
the selector shape, including that no `:checked` appears, since that is the specific
mistake that can never match a hidden input.

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

**Both entries below are RESOLVED.** The 30min tile landed in Story 4.5. The
cross-run orphan was fixed on 2026-07-25 during the venue-gate closure — details
immediately after the original text, which is left intact because its analysis of
*why the naive fix was wrong* is still the reason the eventual fix took the shape
it did.

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

  **RESOLVED 2026-07-25 (venue-gate closure).** Both halves, and the second half
  turned out to be the one that mattered: the ETF catalog had accumulated **17,255
  orphaned partitions** from exactly the venue-change case, not the narrow mixed-path
  sequence the original entry anticipated.

  The fix is not the flag this entry proposed. `qualification_sync` instead enforces
  the equivalence the flag would have duplicated —

      nautilus_id is non-NULL  ⟺  resolution_status is RESOLVED

  — in both directions, and `metadata coverage --gate` fails if it is ever violated.
  A consumer gating on `nautilus_id` is therefore already gating on the status, with
  no join in the hot path and no second source of truth to drift. Concretely:

  - **Forward.** A newly resolved ticker gets its identity without a re-import, which
    is what `apply-overrides` could never do before.
  - **Reverse.** A ticker that loses RESOLVED has its stale identity cleared. This
    was not hypothetical: the closure run cleared **154** tickers that FMP had
    resolved and IBKR could not, each of which would otherwise have stayed silently
    backtestable under a venue nothing vouches for.
  - **Orphaned partitions.** `catalog restamp-venues` moves the bars onto the
    corrected venue — directory *and* parquet footer, since `ParquetDataCatalog`
    finds files by the former and decodes them via the latter.
  - **Recurrence.** `_classify_ticker` now forces a re-import on venue drift. Every
    column it compared (`date_range_end_*`, `bar_count_*`) is venue-independent, so a
    corrected venue previously looked "already complete" and got skipped — which is
    precisely how 17,255 partitions were stranded.

  What this entry called for — a `backtestable` flag or a threaded `resolution_status`
  join — was deliberately not built. The status *is* consulted, but only on the
  failure path, to say which of the three non-backtestable states applies
  (`non_backtestable_reason`), because telling someone their delisted instrument has
  "an unresolved venue" sends them to fix something that is not broken.

  Separately, `chart_bars.py` fell back to the bare unqualified symbol when a ticker
  had no `nautilus_id`. In a named catalog that matches no partition, so the chart
  rendered empty with no explanation. It now fails with the reason.

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

**RESOLVED** — closed by Story 4.5 (`bar_count_30min` on `TickerStatsResponse`, and
the 30-Min tile in `stats_panel.html`). Recorded here because the entry was never
struck when 4.5 landed.

## Migrated from story 5-4-persist-etf-backtest-results-to-the-database (2026-07-22)

This finding lived only inside the Story 5.4 file, so it had no tracker home and was
invisible to anyone reading this list. Recorded here and **RESOLVED 2026-07-25**.

- **Trade rows silently lost for runs ending on an open position**
  [src/services/backtest_persistence.py]. A backtest that finished holding a position
  persisted its run row and its `performance_metrics`, then no trades at all. The
  positions report carries NaN in every exit field for an open position, and the
  writer skipped those rows at `logger.debug` — invisible at the default log level.
  Not ETF-specific; any strategy that ends in the market was affected.

  Fixed by recording open positions with a null exit, which the schema was always
  built for (`exit_price` / `exit_timestamp` are nullable, with a CHECK that tolerates
  NULL). Three latent faults behind the skip were repaired at the same time, each of
  which would have surfaced the moment the skip was removed: `float('nan')` is truthy,
  so `if row["duration_ns"]` never filtered NaN and `int(nan)` would have raised;
  `Decimal("NaN").quantize()` likewise raises; and `str(None)` was persisting the
  literal `"None"` as a closing order id.

  A fourth, more serious fault was found while fixing it: **a failed trade write took
  the run and its metrics down with it.** `bulk_create_trades` flushes, so a failure
  there poisons the session — the existing swallow-and-continue then hit a commit that
  raised, and the outer handler swallowed *that* too. Trade capture is now wrapped in a
  savepoint in both orchestrator paths, so the failure stays local and the run survives,
  which is what the original swallow intended.

## Open: added 2026-07-25 (previously untracked)

- **`DataCatalogService` walks the whole `data/bar/` tree on every request**
  [src/services/data_catalog.py:101 (`__init__` → `_rebuild_availability_cache`);
  src/api/dependencies.py:232-237]. The service is a per-request FastAPI dependency and
  rebuilds its availability cache in `__init__`, with no caching of any kind — no
  `lru_cache`, no `app.state`, no module singleton. Every explorer chart, stats panel,
  and REST call therefore re-stats the entire tree: cheap for a small catalog, heavy for
  `firstrate-etf` (23,060 directories) and heavier still for `firstrate-stocks`. Real but
  non-blocking — deferred to `main` as a follow-up. Likely fix: an `lru_cache`d factory
  keyed on resolved catalog path with an explicit invalidation hook from `write_bars`.

- **`tests/integration/core/test_backtest_catalog_integration.py` is 961 lines**
  (guideline: 500). Flagged in Story 5.5. It splits cleanly along its three test
  classes, with the builders, the `synthetic_catalog` fixture, and the Postgres schema
  harness moving to a shared `conftest.py`. Only the persistence class needs a live
  Postgres, so splitting also lets the other two run without DB setup. Deferred to
  `main`.
