# Deferred Work

Real, non-blocking findings deferred from code reviews. Each entry notes its source and why it was deferred.

## Deferred from: code review of story 3-1-ticker-nautilus-qualified-instrumentid-via-resolved-venue (2026-07-13)

- **Cross-run orphan: existing bars + newly-`VENUE_UNRESOLVED` venue nulls `nautilus_id`**
  [src/services/firstrate/instrument_mapper.py:sync_qualification]. When a ticker with bars imported under a
  provisional/CSV venue on a prior run (no-resolver, fault-fallback, or stocks path) later resolves
  `VENUE_UNRESOLVED`, `sync_qualification` writes `nautilus_id = None` / `exchange = None` while the bars remain on
  disk — leaving a `catalog_instruments` row with `bar_count > 0` but a NULL identity, so the bars are unreachable
  by the backtest loader / explorer. Nulling the provisional id is *required* by Story 3.1 AC3 for the fresh case
  (no bars); the reconciliation for already-imported unresolved tickers (keep the bars, flag non-backtestable,
  don't orphan) is the explicit deliverable of **Story 3.5 — Exclude & Flag Non-Backtestable Tickers**. Story 3.5
  must not null an identity whose partition still holds bars. (Cache-first resolution means a once-`RESOLVED`
  ticker stays resolved, so this only bites the narrow mixed-path cross-run sequence.)

## Deferred from: code review of story 2-7-import-summary-and-progress-reporting (2026-06-29)

- **`ResolutionSummary` has no bucket for a non-throwing `UNRESOLVED` record**
  [src/models/instrument_metadata.py:142-147]. `ResolutionSummary.from_results` (Story 1.6) increments
  `resolved` or `venue_unresolved` by status; a clean-degrade `UNRESOLVED` record (distinct from the
  `VENUE_UNRESOLVED` error record) counts toward neither, so `resolved + venue_unresolved` can under-count the
  tickers actually resolved this run, with no "unresolved" line in the summary table. Pre-existing Epic-1
  aggregation semantics — Story 2.7's import-summary wiring only reads `from_results`. Revisit alongside the
  Epic-3 venue worklist / completeness gate, where unresolved-state reporting is in scope.
