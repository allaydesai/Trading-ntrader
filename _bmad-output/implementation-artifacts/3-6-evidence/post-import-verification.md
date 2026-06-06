# Story 3-6 — Post-Import Verification Evidence

Captured: 2026-06-03 (verify-and-close path; see Dev Notes in the story file).

## Summary

The `e2e-test` FirstRate catalog was found **already re-imported through the timezone-corrected
parser** (`FirstRateCsvParser._parse_timestamp`, fixed in Story 3-4 commit `2171e02`,
2026-05-04). Partition mtimes are 2026-05-15, just after the Story 3-6 scaffolding commit
`ed002cb`. No destructive re-import was required — verification confirms every partition holds
TZ-correct timestamps.

Schema side: the `data_quality_flag` migration (`79f6e07bee8b`) is applied (`alembic current`
= head) and its backfill flagged the affected runs.

## AC #4 / #5 — Bar counts and date-range edges

Source: `post-import-metadata.json` (read from `catalog_instruments` via the snapshot tool).

| Ticker | 1-DAY | 1-HOUR | 5-MIN | 1-MIN | date_range_start (ET) | date_range_end (ET) |
|--------|------:|-------:|------:|------:|-----------------------|---------------------|
| AAPL | 6,621 | 93,875 | 990,036 | 4,073,009 | 2000-01-04 09:30 -05:00 | 2026-05-01 19:59 -04:00 |
| AMZN | 6,621 | 87,316 | 815,905 | 3,331,198 | 2000-01-04 09:30 -05:00 | 2026-05-01 19:59 -04:00 |
| MSFT | 6,621 | 89,705 | 868,248 | 3,457,664 | 2000-01-03 09:30 -05:00 | 2026-05-01 19:59 -04:00 |
| NVDA | 6,621 | 83,771 | 822,184 | 3,485,515 | 2000-01-03 09:30 -05:00 | 2026-05-01 19:59 -04:00 |
| TSLA | 3,984 | 57,913 | 587,430 | 2,487,497 | 2010-06-29 11:25 -04:00 | 2026-05-01 19:59 -04:00 |

- **20/20 partitions non-zero.** Aggregate ≈ **21.36M bars** (sum of all counts above =
  21,361,734). The memory note's "~21.25M" was for an earlier 2018-bounded snapshot; data now
  spans 2000-01-03 → 2026-05-01, so a modestly higher count is expected, not a red flag.
- **No 0.5% pre/post delta check possible** — the ephemeral `/tmp` pre-import snapshot is gone
  and the catalog was already re-imported. TZ correctness (below) is the decisive evidence
  instead; bar-count sanity is established by all-non-zero + monotonic full-history coverage.
- **date_range edges are ET-correct**, stored with the proper `America/New_York` offset
  (`-05:00` EST in January, `-04:00` EDT in May/June). A corrupt catalog would show
  naive-UTC edges (e.g. `00:00Z` daily, `09:30Z` intraday).

## AC #6 — TZ correctness across all 20 partitions

Read first + last bar `ts_event` from each partition's parquet, converted UTC → ET. A corrupt
catalog would place daily bars at 19:00/20:00 ET (prev day) and shift intraday opens.

| Ticker | TF | first bar (ET) | last bar (ET) | verdict |
|--------|----|----------------|---------------|---------|
| AAPL | 1-DAY | 2000-01-04 00:00 | 2026-05-01 00:00 | OK |
| AAPL | 1-HOUR | 2000-01-04 09:00 | 2026-05-01 19:00 | OK |
| AAPL | 5-MIN | 2000-01-04 09:30 | 2026-05-01 19:55 | OK |
| AAPL | 1-MIN | 2000-01-04 09:30 | 2026-05-01 19:59 | OK |
| AMZN | 1-DAY | 2000-01-04 00:00 | 2026-05-01 00:00 | OK |
| AMZN | 1-HOUR | 2000-01-04 09:00 | 2026-05-01 19:00 | OK |
| AMZN | 5-MIN | 2000-01-04 09:30 | 2026-05-01 19:55 | OK |
| AMZN | 1-MIN | 2000-01-04 09:30 | 2026-05-01 19:59 | OK |
| MSFT | 1-DAY | 2000-01-04 00:00 | 2026-05-01 00:00 | OK |
| MSFT | 1-HOUR | 2000-01-03 09:00 | 2026-05-01 19:00 | OK |
| MSFT | 5-MIN | 2000-01-03 09:30 | 2026-05-01 19:55 | OK |
| MSFT | 1-MIN | 2000-01-03 09:30 | 2026-05-01 19:59 | OK |
| NVDA | 1-DAY | 2000-01-04 00:00 | 2026-05-01 00:00 | OK |
| NVDA | 1-HOUR | 2000-01-03 09:00 | 2026-05-01 19:00 | OK |
| NVDA | 5-MIN | 2000-01-03 09:30 | 2026-05-01 19:55 | OK |
| NVDA | 1-MIN | 2000-01-03 09:30 | 2026-05-01 19:59 | OK |
| TSLA | 1-DAY | 2010-06-30 00:00 | 2026-05-01 00:00 | OK |
| TSLA | 1-HOUR | 2010-06-29 11:00 | 2026-05-01 19:00 | OK |
| TSLA | 5-MIN | 2010-06-29 11:25 | 2026-05-01 19:55 | OK |
| TSLA | 1-MIN | 2010-06-29 11:25 | 2026-05-01 19:59 | OK |

- Daily bars sit at **midnight ET** across all tickers ✓ (DST-aware: `-05:00` in winter,
  `-04:00` for TSLA's June IPO).
- Intraday opens at **09:30 ET** (regular session) and extended-hours data runs to **19:59 ET** ✓.
- **TSLA IPO day** (2010-06-29): first intraday bar at **11:25 ET** matches its first-trade time;
  its first daily bar is 2010-06-30. Correct, not corrupt.
- **20/20 partitions TZ-correct.**

## AC #7 — Affected `backtest_runs` flagged

`SELECT ... WHERE data_quality_flag IS NOT NULL` (via project sync session):

| run_id | data_source | created_at | flag |
|--------|-------------|------------|------|
| 3986dd75-80b8-4219-b37d-a2cbc40a85b9 | catalog:e2e-test | 2026-04-19 15:26 -04:00 | tz_corrupted_pre_3.6 |
| b4ab6318-49fc-4d3b-980b-0720e742ebf6 | catalog:e2e-test | 2026-04-20 17:01 -04:00 | tz_corrupted_pre_3.6 |
| 5f7b4ff0-bb36-43ee-acb1-d2f513d71d35 | catalog:e2e-test | 2026-05-01 08:59 -04:00 | tz_corrupted_pre_3.6 |

- **3 rows flagged**, matching exactly the run_ids enumerated in the story spec.
- These are **all** catalog-sourced runs in the DB (3 of 3); all predate the 2026-05-04 fix, so
  none are missed and none are false-positives. Total runs in DB: 126.
- UI banner rendering is covered by passing tests:
  `tests/ui/test_backtest_detail_models.py::TestDataQualityFlag` and
  `tests/component/api/test_backtest_detail_routes.py::TestDataQualityBanner`.

## Deferred (non-blocking for Epic 4)

- **Task 7** — IBKR-vs-FirstRate parity harness re-run (`tests/integration/core/...` with
  `IBKR_AVAILABLE=1`). Needs IBKR Gateway; parity was already characterized in Story 3-4. Folds
  into the Story 3-7 follow-up.

## Conclusion

The data-level Epic 4 blocker is resolved: all 20 partitions are TZ-correct and the 3 pre-fix
runs are flagged. Story 3-6 acceptance criteria #4–7 are satisfied.
