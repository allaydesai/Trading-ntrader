# Story 4-6 — Explorer Accuracy Verification Evidence

Captured: 2026-07-17.

## Summary

Story 4-6 is the **trust gate** that closes Epic 4: prove the Data Explorer renders the
imported ETF catalog faithfully — bars, metadata, and the new 30-minute timeframe — before
Epic 5 backtests on it. The verification chain is:

```
TradingView-grade source ──(Story 2.5: OHLC sanity + row-count parity + sample-point)──▶ Parquet catalog
Parquet catalog ──────────(Story 4.6: render fidelity, THIS story)─────────────────────▶ Explorer UI
```

Story 2.5 already proved **source ↔ catalog** parity at import time. Story 4-6 pins the last
unproven link — **catalog ↔ explorer render** — as an *exact identity* (zero tolerance, because
it is a serialization, not a cross-source comparison), and pairs it with the operator's live
TradingView spot-check. Together they establish "what the operator sees == the external
reference," end to end.

## What is machine-checked here (executed & green)

The accuracy contracts are reusable, pure primitives in `src/api/explorer_verification.py`,
exercised two ways: as unit tests over stub bars/panels, and end-to-end by driving the *real*
`/explorer/chart-panel` and `/explorer/metadata-panel` routes and running the primitives over
their rendered output.

| AC | What it proves | Where verified | Status |
|----|----------------|----------------|--------|
| **AC1** — OHLC bars match reference | Chart transform (`bar.<f>.as_double()` → `Candle`, `int(ts_event/1e9)` → `time`, `int(volume.as_double())`) is an **exact identity** — no rounding drift, no ns-vs-s bug, no dropped/extra bar. Awkward decimals (`474.503`) and a large odd volume (`45_000_001`) survive the round-trip through `bars_json`. | `verify_candle_fidelity` + `tests/unit/api/test_explorer_verification.py`, `tests/component/api/test_explorer_accuracy.py::TestChartRenderFidelity` | ✅ green |
| **AC2** — full vs sparse metadata | Full rows show real name/venue/sector; sparse fields collapse to a **muted `N/A`** (`text-slate-500`), never a blank cell; venue is **never** the descriptive `N/A` sentinel — an unresolved venue is the distinct amber `Unresolved` pill; a DB fault degrades to the empty-state, never a 500. | `verify_metadata_na_contract` + `tests/component/api/test_explorer_accuracy.py::TestMetadataAccuracy` | ✅ green |
| **AC3** — 30min renders | `tf=30m` returns the `30-MINUTE-LAST` bars, the 30m toolbar button is `aria-pressed="true"` and enabled, and all five labels `D / 1H / 30m / 5m / 1m` are present; its bars pass fidelity. | `tests/component/api/test_explorer_accuracy.py::TestThirtyMinuteRendering` | ✅ green |

Run:

```bash
uv run pytest tests/unit/api/test_explorer_verification.py \
              tests/component/api/test_explorer_accuracy.py -q
# 23 passed (13 unit + 10 component)
```

### Why render-fidelity is the right internal proxy for "matches TradingView"

The explorer never re-derives OHLC — it forwards `catalog_service.query_bars` output straight
into `Candle` via `.as_double()` (`src/api/ui/explorer.py:507-519`). So the only way the explorer
could disagree with TradingView *given a correct catalog* is a transform bug (rounding, unit,
timestamp scaling, a dropped/duplicated bar). `verify_candle_fidelity` catches exactly those.
The catalog-vs-TradingView question itself is Story 2.5's territory (source parity) and is not
re-opened here; 4-6 proves the UI is a faithful window onto that already-verified catalog.

## Fixture set (real-world tickers named for the operator run)

The machine checks use mock instruments/bars; the live operator spot-check below uses real
tickers chosen to exercise every AC dimension:

| Fixture | Timeframe | Exercises |
|---------|-----------|-----------|
| **QQQ** | Daily | Deep-liquidity, full metadata — AC1 daily + AC2 full |
| **IWM** | 30min | The new native timeframe — AC1 + AC3 |
| **TQQQ / SQQQ** (leveraged/inverse) | 1min | Thin/volatile stress case — AC1 at the finest resolution |
| A **full-metadata ETF** (e.g. SPY) | any | AC2 full name/venue/sector |
| A **sparse-metadata ETF** | any | AC2 muted `N/A` for missing descriptive fields |
| An **unresolved-venue ticker** | any | AC2 distinct amber `Unresolved` pill (never `N/A`) |

## Operator step — live TradingView spot-check (agent-browser)

**Not executed in the autonomous harness.** This worktree has **no live ETF catalog** (the local
`data/catalog` holds only 3 stock tickers — AAPL/MSFT/GOOGL — at 1-DAY), and hitting live
TradingView is external I/O outside the harness's bounds. No TradingView numbers or screenshots
are fabricated. The procedure below is the reproducible operator run this record enables, to be
performed against a full ETF catalog with the dev server up (`make web`, `http://127.0.0.1:8000`):

For each fixture in the table above:

1. `agent-browser snapshot -i` the explorer, select the catalog + ticker, pick the timeframe.
2. `agent-browser wait --text "<ticker>"` then snapshot the chart panel; read the first and last
   visible bar's OHLC via the chart tooltip.
3. Open the same ticker/timeframe/date on TradingView; compare the first and last visible bar's
   OHLC. Consolidated-vs-single-venue OHLC diffs are normal on spot-checks (see project memory
   `reference_chart_time_vs_tradingview`); the chart axis renders ET while data is stored UTC.
4. Check the metadata panel: full fixtures show real name/venue/sector; the sparse fixture shows
   muted `N/A`; the unresolved-venue fixture shows the amber `Unresolved` pill.
5. `agent-browser screenshot <slot>.png` for the verification record.

### Screenshot evidence slots (fill on operator run)

- [ ] `qqq-daily.png` — QQQ daily chart vs TradingView
- [ ] `iwm-30min.png` — IWM 30-minute chart (AC3 timeframe)
- [ ] `tqqq-1min.png` — leveraged ETF 1-minute chart
- [ ] `metadata-full.png` — full metadata panel
- [ ] `metadata-sparse-na.png` — sparse metadata rendered as muted `N/A`
- [ ] `metadata-unresolved-venue.png` — amber `Unresolved` venue pill

## Conclusion

The internal catalog↔render fidelity, N/A three-state, and 30-minute-rendering ACs are
**machine-verified and green** (23 passing tests). Combined with Story 2.5's source↔catalog
parity, the explorer is a proven-faithful window onto the ETF catalog. The live TradingView OHLC
spot-check and real-ETF screenshots are the operator step this record scripts, to be captured
against a full ETF catalog — not run in the autonomous harness. See
`verification-summary.json` for the structured record.
