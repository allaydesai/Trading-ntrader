# Tasks

## 1. Futures parser
- [x] 1.1 Component (failing first): `tests/component/services/firstrate/test_firstrate_futures_parser.py`:
      registration, 7-column daily, mixed/excess decimals, ZN 6 decimals, ET->UTC parity with the
      equity parser across DST and the Globex 18:00 open, chunking, duplicates, out-of-order
      rows across chunks, invalid rows counted, empty file.
- [x] 1.2 Implement `FirstRateFuturesParser` and register it.

## 2. Import service
- [x] 2.1 Component (failing first): `tests/component/services/firstrate/test_futures_import_service.py`
      against a real tmp catalog: parts per chunk, fixed precision on read-back, spec-filled
      row, daily 7-column, re-run skip, grown source overwrite, partial Parquet reimport,
      unknown root, empty file, invalid-row warnings.
- [x] 2.2 Implement `FuturesImportService`.
- [x] 2.3 Unit: source probe accepts 7-column rows.

## 3. CLI
- [x] 3.1 Unit/component (failing first): futures default timeframes, dispatch to
      `run_futures_import`, dry-run accepts 7 columns, per-timeframe commits, no DB -> 2.
- [x] 3.2 Implement `import_futures.py`, the dispatch and dry-run column tolerance.

## 4. Equity precision fix
- [x] 4.1 Component regression (failing first): `test_equity_parser_fixed_precision.py`
      (mixed decimals round-trip through a real catalog; uniform files unchanged).
- [x] 4.2 Fixed precision per file in `FirstRateCsvParser`.

## 5. Repository
- [x] 5.1 Component (failing first): upsert copies the futures columns.
- [x] 5.2 Add them to `_UPSERT_FIELDS`.

## 6. Integration and real import
- [x] 6.1 Integration (`--forked`): trimmed real ES 1-hour (DST week) and ZN daily import,
      full read-back equals source, engine accepts the bars, re-run skips.
- [x] 6.2 Manual: import all five roots into `firstrate-futures` (1day, 1hour, ES 1min with
      peak RSS measured), row counts vs source line counts.

## 7. Docs
- [x] 7.1 README import section, `docs/agent/data-pipeline.md`.
