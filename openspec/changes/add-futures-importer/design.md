# Design: streaming FirstRate futures importer

## Source format

`/srv/market-data/raw/futures/{ROOT}_full_{1min|5min|30min|1hour|1day}_continuous_ratio_adjusted.txt`,
headerless, US/Eastern wall clock. Intraday rows: `ts,O,H,L,C,V`. Daily rows add open
interest: `date,O,H,L,C,V,OI`. Files are flat in one directory (equities use alphabetical
subdirectories); discovery accepts both depths. Up to 6.4M rows per file (ES 1-minute).

## Parsing

`pd.read_csv(..., names=[ts,O,H,L,C,V,OI], chunksize=N, on_bad_lines="skip")` reads either
layout; OI is never used. Per chunk:

1. **Timestamps.** Rows longer than `YYYY-MM-DD` parse as `%Y-%m-%d %H:%M:%S`, others as
   daily. `tz_localize("America/New_York", ambiguous=True-array, nonexistent=+1h)` then UTC.
   That matches `FirstRateCsvParser._parse_timestamp` (zoneinfo, `fold=0`) exactly: an
   ambiguous fall-back time resolves to its first (daylight) occurrence and a non-existent
   spring-forward time is shifted an hour forward (so `02:30` -> `07:30Z`). A test pins
   parity at both DST edges and at the Sunday 18:00 Globex open. CME is closed at 01:00-03:00
   ET on Sundays, so neither case occurs in real data.
2. **Fixed precision.** Prices are coerced to float and rounded half-even to the spec's
   `continuous_price_precision`, then built as `Price(value, precision)`. Every bar of a root
   has the same precision in every timeframe, so the catalog's "first bar sets the schema
   precision" behaviour is harmless. Float rounding is exact here: the series carry at most
   6 decimals and at most ~7 integer digits, far inside a double's 15-16 significant digits.
3. **Validation** on the rounded values (so the checks hold for what is stored): parseable
   timestamp and numbers, prices > 0, `high >= low`, open/close within `[low, high]`,
   volume >= 0. Invalid rows are dropped and counted into `last_validation`, which the
   existing `collect_ohlc_warnings` turns into result warnings (first 50 errors kept).
4. **Order.** Stable sort by timestamp, drop duplicate timestamps keeping the last (same
   rule as the equity parser). A row at or before the previous chunk's last bar is dropped
   and counted as out of order: each chunk becomes its own Parquet part and Nautilus requires
   parts' intervals to be disjoint. The real files are sorted with no duplicates.

Chunk size defaults to 250k rows. Measured on ES 1-minute: ~2 s per 500k rows, and 500k
chunks peaked at ~790 MB RSS while parsing; 250k keeps a chunk at ~150 MB above the ~200 MB
interpreter + Nautilus baseline.

## Import flow (`FuturesImportService`)

Subclass of `ImportService`, reusing its batch loop, progress logging, timeframe filter and
classifier. Per root and timeframe:

1. Upsert the `catalog_instruments` row from the spec: `ticker = ROOT`,
   `nautilus_id = ROOT.VENUE`, `asset_class = FUTURES`, `exchange = venue`, `name`,
   `multiplier`, `tick_size`, `currency`, `contract_kind = continuous`, `expiry = NULL`,
   `roll_rule = firstrate-ratio-adjusted`. Re-applied on every run so a spec edit reaches
   the row. An unknown root fails that file loudly (`Unknown futures root 'XX'`).
2. Classify with the inherited classifier (per-timeframe end vs source last date). A skip
   additionally requires that no `.import-incomplete` marker sits in the bar type's
   directory and that its Parquet footers are readable and hold exactly the recorded bar
   count. Equities trust the metadata row alone (ADR-5), but a streamed write that dies after
   the delete would otherwise leave a partial series behind a row that still reads complete.
   The count alone cannot tell an interrupted rewrite whose parts happen to add up to the old
   count from a finished one, hence the marker: touched before the delete, removed only after
   verification and the coverage update. Nautilus only lists `*.parquet`, so it ignores it.
3. `catalog.delete_data_range(Bar, bar_type)` (whole range: files are removed, not read),
   then one `catalog.write_data(chunk.bars)` per chunk.
4. Verify without a full read-back: the sum of `num_rows` over the parts' footers must equal
   the rows written, and up to 12 chunk-edge bars (first and last always) are read back with
   `catalog.bars(start=ts, end=ts)` and compared field by field, including precision.
5. Record per-timeframe start/end/count on the row; `date_range_end` is recomputed as the
   latest of the per-timeframe ends (so it follows a regressed source too), then the marker
   is removed.

The CLI commits after every timeframe pass.

## Equity precision fix

`FirstRateCsvParser` already reads the whole file; it now computes the widest decimal count
of any price in the file (capped at Nautilus' 9) and quantizes every price to it before
`Price.from_str`. A file whose prices all carry the same decimals produces identical bars.
Precision is per file, so an instrument's daily and intraday files can still differ (as
today); the backtest loader already infers precision from the bars it loads.
