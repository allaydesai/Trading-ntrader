# Deferred Work

Real, non-blocking findings deferred from code reviews. Each entry notes its source and why it was deferred.

## Deferred from: code review of story 2-7-import-summary-and-progress-reporting (2026-06-29)

- **`ResolutionSummary` has no bucket for a non-throwing `UNRESOLVED` record**
  [src/models/instrument_metadata.py:142-147]. `ResolutionSummary.from_results` (Story 1.6) increments
  `resolved` or `venue_unresolved` by status; a clean-degrade `UNRESOLVED` record (distinct from the
  `VENUE_UNRESOLVED` error record) counts toward neither, so `resolved + venue_unresolved` can under-count the
  tickers actually resolved this run, with no "unresolved" line in the summary table. Pre-existing Epic-1
  aggregation semantics — Story 2.7's import-summary wiring only reads `from_results`. Revisit alongside the
  Epic-3 venue worklist / completeness gate, where unresolved-state reporting is in scope.
