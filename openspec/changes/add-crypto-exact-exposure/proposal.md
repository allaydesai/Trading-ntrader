# Proposal: exact peak exposure for spot crypto runs

## Why

Crypto M5 (PR #75) reports peak notional and peak exposure (notional / equity) from the trade
rows. A row keeps a position's largest quantity and its open time, not its fill history, so a
position that scales in is counted at its largest size from its open, and a position opened
and closed at one timestamp counts as held then: an upper bound, and the report note says so
(design section 10 of `add-crypto-backtesting`: "an exact figure needs a persisted
per-position quantity series"). Its equity is the starting capital plus closed-trade P&L, which
leaves out the costs of positions still open, so the old exposure is not strictly an upper
bound either (see the smoke below).

**Approval: Allay approved this follow-up on 2026-11-25, including minimal changes to the
dashboard's protected files.**

## What changes

- **Record** (`src/core/crypto_costs.py`): `CryptoCommissionModel.get_commission` is asked once
  per fill, just before Nautilus emits it, so it numbers the fills it charges in the run's cache
  (`ntrader.crypto_fill_count`, `ntrader.crypto_fill.<n>` = the client order id). After the run,
  `position_series(cache, ...)` joins those numbers with the orders' `OrderFilled` events and
  `crypto_snapshot` saves, per pair, `crypto_pairs[<id>].position_series`: one entry per fill,
  `[seq, ts_ns, position_id, position_qty, fill_px, commission_usd]` (fill number across pairs,
  time, the fill's position and its signed quantity after the fill, price, USD charged). The
  crypto venue is `HEDGING`, so the series is per position, grouped per pair. The count lives
  in the cache, so fee models of several venues share one numbering. The series is left out
  (the run reports the upper bound rather than a wrong figure) if the numbers and the fill
  events don't match one for one, a fill has no position id, or the replay misses the
  account's USD balance by more than a cent per fill.
- **Compute** (`src/services/crypto/exposure.py`, new, pure): replay the fills in `seq` order,
  keeping each position's quantity and average open price (Nautilus' `avg_px_open`) and the
  USD balance (starting capital plus each fill's realised P&L in whole cents, less its charge,
  as the account books it). After every fill: notional = sum of `|qty| x avg open price`
  (cost basis, the M5 figure per trade); exposure = notional / balance. Peaks are the maxima.
- **Report** (`src/services/crypto/returns.py`, `display.py`): `summarise_run` uses the replay
  when every pair carries a series (`RunSummary.exposure_exact`), else the M5 upper bound.
  The note says "exact, replayed from every fill" for an exact run and keeps the M5 wording
  otherwise; `report_fields` gains `peak_exposure_basis` (`exact` | `upper_bound`), so MCP
  `get_run`/`compare_runs`/`export_results`, the report JSON/CSV (`crypto_peak_exposure_basis`),
  `show`, `report` and the dashboard panel all follow.
- **Display**: `backtest show` and the dashboard's parameters list print the raw snapshot; a
  pair's series shows there as `"N fills (exact peak exposure)"`
  (`display.snapshot_params`). This is the only protected-file change
  (`src/api/models/backtest_detail.py`, one call); `FROZEN_VIEWS` is regenerated for that file.

## Follow-up: store the peaks, not the series

Approved by Allay on 2026-11-25 (crypto decision follow-ups). A run no longer saves
`crypto_pairs[<id>].position_series`: `crypto_snapshot` replays the series when the run is saved
(from the accounts' starting balance, after the same one-cent-per-fill check) and saves only
`config_snapshot.crypto_peaks = {"basis": "exact", "peak_notional_usd", "peak_exposure"}` as
exact decimal strings. Readers use saved peaks first, then a PR #79 run's series (replayed as
before), then the M5 upper bound. No migration; the figures are identical (smoke below).

## Impact

- Persistence: no migration. The series lives in the existing JSONB `config_snapshot` (as M5's
  `orders_below_minimum` does). Runs saved before this change have no series and keep the
  upper bound and its note; no backfill, no row changes. Size: one short list per fill (about
  60 bytes), e.g. 36 fills for a 2-year daily SMA run.
- Non-crypto runs: no new keys, no output change; config hashes are computed from the request
  and do not change for any run.
- MCP/JSON contract: one added key, `crypto.peak_exposure_basis`.
- Not in scope: mark-to-market exposure between fills (the figure stays cost basis over the
  booked balance, as M5 defined it).
