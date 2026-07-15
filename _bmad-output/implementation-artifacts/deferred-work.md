# Deferred Work

Real, non-blocking findings deferred from code reviews. Each entry notes its source and why it was deferred.

## Deferred from: code review of story 3-1-ticker-nautilus-qualified-instrumentid-via-resolved-venue (2026-07-13)

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
