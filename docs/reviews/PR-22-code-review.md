# Code Review — PR #22: FirstRate import, named catalogs, data explorer & catalog backtesting

- **PR:** #22 — `feat: FirstRate data import, named catalogs, data explorer & catalog backtesting (Phase 1 MVP)`
- **Branch:** `data-import-explore-backtest` → `main`
- **Reviewed:** 2026-06-05
- **Size:** 193 files, ~39.7k additions (most are BMAD planning docs). Source scope reviewed: **52 changed files under `src/`, ~6.6k new lines.**
- **Method:** 7 finder angles (3 correctness: line-by-line / removed-behavior / cross-file; plus reuse, simplification, efficiency, altitude, security) → 42 candidates → dedup → one verifier per surviving candidate against the actual code. Findings below are only those that passed verification.

## Summary

Well-engineered, heavily-tested PR with prior review already baked into the commit history. The findings below survived verification. Two touch **data integrity in a financial system** and should be fixed before merge (#1, #2). The rest are graceful-failure / correctness / UI issues plus maintainability themes.

> Status legend: ☐ open · ☑ fixed. Update as items are addressed.

---

## 🔴 High — data integrity / correctness

### ☑ 1. Re-import appends a duplicate copy of bars and corrupts the catalog
**`src/services/firstrate/import_service.py:229-234`**

The `"reimported"` classification path calls `catalog.write_data(bars)` with **no delete/clear of the existing partition**. Nautilus `write_data` appends a new parquet part, so the read-back at step 5 (`catalog.bars(...)`) returns *old + new* bars. `_verify_row_count` then fails (length mismatch), the ticker is marked `"failed"`, and metadata is **not** updated — but the duplicated bars are now permanently on disk. On the next run the classifier still sees stale metadata (`source > metadata` → `"reimported"`) and appends **again**. Any backtest over that window then double-counts every bar (corrupt PnL / drawdown / trade counts) with no warning.

**Fix:** delete the instrument's existing parquet for that bar_type before writing, or write to a temp dir and swap atomically.

### ☑ 2. `bar_count_5min` silently dropped on the upsert update-branch
**`src/db/repositories/catalog_instrument_repository.py:57-59` (async), `:292-294` (sync)**

The update branch copies `bar_count_daily/hourly/minute` onto the existing row but omits `bar_count_5min`. Inserts persist it (via `session.add`), but any upsert where the row already exists keeps the stale 5-min count. The explorer gates the timeframe toolbar on `bar_count > 0`, so **5-minute charts silently disappear for re-imported tickers**. This is the "added a column, forgot a copy site" failure — and it's the column commit `436074269` added specifically to stop 1-min/5-min sharing a count.

**Fix:** add `existing.bar_count_5min = instrument.bar_count_5min` to both repos. (Root cause is the per-timeframe-column shape — see Maintainability.)

---

## 🟠 Medium — wrong behavior / bad failure modes

### ☑ 3. CLI catalog-validation errors crash with a raw traceback (exit 1) instead of a usage error
**`src/cli/commands/backtest.py` (first `try` catches only `click.UsageError`) + validators in `src/models/backtest_request.py:133-135, 170-173`**

`BacktestRequest(...)` is constructed inside `resolve_backtest_request`, whose `try` only catches `click.UsageError`. The new validators raise `ValidationError` (not a `ValueError` subclass at the `pydantic_core` level), so `--catalog 'bad name!'` or `--catalog x --data-source ibkr` produces an **unhandled Pydantic traceback, exit code 1** — never reaching the `startswith("Unknown catalog")` handler (which guards a later stage).

**Fix:** catch `ValidationError` at construction and convert to `click.UsageError` (exit 2).

### ☑ 4. Web and CLI compute different backtest windows for the same `end_date`
**`src/api/ui/backtests.py:285` vs `src/cli/commands/_backtest_helpers.py:267-270`**

Web uses `datetime.combine(end_date, datetime.max.time())` (inclusive of the final day); the CLI parses `--end` to midnight `00:00:00` and never bumps it, and the shared model doesn't normalize. So `--end 2024-12-31` on the CLI **excludes the entire last trading day** that the web UI includes — an off-by-one-day discrepancy that directly undermines the IBKR-vs-FirstRate parity work.

**Fix:** normalize end-of-day in one shared place (`BacktestRequest.from_cli_args` or the model).

### ☑ 5. Indicator overlay (and trade chart) query the default catalog for named-catalog runs
**`src/api/rest/indicators.py:247,276` (also the trades/candlestick detail route)**

Both branches build `DataCatalogService()` (default `NAUTILUS_PATH`) with the bare `instrument_symbol`, never reading `config_snapshot.catalog_name` / the `catalog:<name>` prefix. For a named-catalog backtest the bars aren't there → `DataNotFoundError` is swallowed by the broad `except` (`:264-271`) → overlay renders **empty even though the backtest ran on real data**. The hardcoded `"1-DAY-LAST"` (`:259,288`) also mismatches intraday runs.

**Fix:** thread the run's catalog_name + actual bar_type into the indicator/trade detail routes.

### ☑ 6. Corrupt parquet 500s the chart panel instead of degrading gracefully
**`src/api/ui/explorer.py:454-463`**

`chart_panel_fragment` catches only `DataNotFoundError`, but `query_bars` also raises `CatalogCorruptionError` (`src/services/data_catalog.py:553`). The stats path *does* catch it (`stats_service.py:89`), so this is an inconsistency: a corrupt file returns HTTP 500 on the chart fragment rather than the "no data" empty state.

**Fix:** also catch `CatalogCorruptionError` in `chart_panel_fragment`.

### ☑ 7. Missing `CATALOG_BASE_PATH` silently scans the current working directory
**`src/config.py:153-154` + `src/cli/commands/_backtest_helpers.py:363`**

`catalog_base_path` defaults to `""`; `Path("")` resolves to `.`, so `CatalogManager` scans CWD and `resolve_catalog` builds `Path("e2e-test")` relative to CWD instead of failing fast. Users get a confusing "available catalogs" list of whatever dirs happen to be in CWD.

**Fix:** validate the setting is non-empty before use (fail fast with a clear message).

### ☑ 8. Daily volume is truncated; documented OHLC validation never runs
**`src/services/firstrate/parsers/firstrate_csv_parser.py:158` vs `:160`; `src/services/firstrate/parsers/base.py:126-195`**

Daily uses `Quantity.from_int(int(float(row.volume)))` (truncates `1.23e6` / fractional volume), intraday uses `Quantity.from_str(row.volume)` (exact) — inconsistent precision for the same instrument, and the module docstring itself flags "float volume notation" as a known FirstRate quirk. Separately, `BaseParser.validate_bars()` (checks high<low, non-positive prices, negative volume) is **never called by `parse_file`** — only tests call it, so the documented integrity gate is dead in the real import path.

**Fix:** use `Quantity.from_str` consistently (or document/round deliberately); wire `validate_bars()` into `parse_file`.

---

## 🟡 Low — UI correctness / defense-in-depth

### ☐ 9. `coverage_pct` measures against `now`, not the instrument's data lifetime
**`src/api/models/explorer.py:122-127`**

Denominator is `now - date_range_start`, so a delisted ticker (data 2005-2010) reports ~23% coverage despite complete data, and nothing ending in the past can read 100%. Misleading metric. **Fix:** define what "coverage" means and compute against the intended span (e.g. trading days within the instrument's own range).

### ☐ 10. `has_company_profile` is contradictory across the two stats endpoints
**`src/api/stats_service.py:117-119`; call sites `rest/explorer.py:54-55` vs `ui/explorer.py:154`**

Computed from *whether repos were passed* + instrument existence, not field population. REST `/stats` passes repos → `True`; HTMX `/stats-panel` passes none → `False` for the same ticker. (`has_dividends`/`has_splits` collapse to `False` on the no-repo path too.) **Fix:** compute flags from actual data presence and pass repos consistently from both call sites.

### ☐ 11. Path traversal on import `--catalog` (defense-in-depth)
**`src/services/firstrate/catalog_manager.py:51`; `src/cli/commands/import_data.py:389-393`**

The name becomes a directory via `self._base_path / name` with no `..`/charset check. The only sanitizing validator (`[A-Za-z0-9_-]+`) lives on `BacktestRequest` (read path), not the import path. Requires local CLI access, but a poisoned `catalog_instruments` row resolves out-of-root at backtest time. **Fix:** apply the same charset/`..` validation on the import write path.

### ☐ 12. `explorer_return` double-`unquote` on the detail GET path (latent)
**`src/api/ui/backtests.py:156`**

The value is FastAPI-decoded once, then `unquote()`'d again — corrupts restored search/filter state containing literal `%`/`+`. The run-form submit flow is correctly balanced; only the `backtest_detail` render path double-decodes. **Fix:** drop the redundant `unquote()` on the already-decoded query param.

### ☐ 13. IBKR `connect()` dropped the `sleep(2)` stabilization wait (plausible)
**`src/services/ibkr_client.py:239-244`**

The client_id rotation rewrite removed the post-connect `await asyncio.sleep(2)`; `account_id`/`server_version` are now read immediately, so `ntrader data connect` may show `Account ID: N/A`. Depends on whether Nautilus populates those synchronously by the time `connect()` resolves. **Fix:** poll/await those attributes (or restore a bounded wait) before displaying.

---

## Maintainability themes (not blocking)

- **Duplicated logic across the Nautilus-free seam.** `_parse_timestamp` is copied verbatim in `firstrate_csv_parser.py` and `source_probe.py`; `_extract_ticker` exists 3× (import_service / dry_run / supplementary_discovery); `_read_lines` 3×. The timezone parser is the one whose drift previously caused a 24× parity bug — extract it to a shared pure-Python module so the two copies can't diverge.
- **Timeframe mapping lives in ~5 parallel tables** (`TIMEFRAME_MAP`, `_FILENAME_TIMEFRAME_MAP`, `_TIMEFRAME_FIELD_MAP`, `TIMEFRAME_EXPLORER_TO_RUN_FORM`, `ExplorerTimeframe`), each with a "must stay in sync" comment. Adding a Phase-2 timeframe means editing all 5; the maps already disagree (`minute`/`1min` alias vs filename `_1min_`). A single timeframe registry would also eliminate finding #2's root cause (`bar_count_*` as 4 hardcoded columns).
- **Named-catalog as a bypass rather than an abstraction.** The `.NAMED_CATALOG` sentinel suffix on `instrument_id` + the `catalog:<name>` string-prefix on `data_source` are two redundant encodings reverse-engineered with `startswith`/`split`. A `DataSource` strategy (each owning its loader) would remove the suffix-stripping fragility and the catalog-first short-circuit that forced the cross-field validator.
- **Efficiency on the explorer hot path.** `chart_panel_fragment` queries a windowed bar set, then `_build_ticker_stats` re-queries the *full* history to compute price min/max (millions of rows for a minute ticker), and the three awaits run sequentially. Push min/max down as an aggregate, or reuse the already-fetched bars.

---

## Notes / scope

- BMAD planning docs (~33k of the additions) were not deep-reviewed.
- One refuted candidate worth recording: the `data_quality_flag` column **does** ship a migration (`alembic/versions/79f6e07bee8b_add_data_quality_flag_to_backtest_runs.py`) — not a missing-migration bug.
- No inline PR comments were posted and no fixes were applied during this review.
