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
