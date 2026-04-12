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
