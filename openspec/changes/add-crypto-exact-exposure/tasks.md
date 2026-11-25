# Tasks

## 1. Replay
- [x] 1.1 Unit (failing first): `tests/unit/services/crypto/test_exposure.py`: a scripted
      two-pair fill sequence (scale-in, partial reductions) gives the exact peak and differs
      from the trade-row upper bound; hedged positions keep their own cost; engine order across
      pairs; a round trip inside one bar counts; P&L booked in whole cents.
- [x] 1.2 `src/services/crypto/exposure.py`; `returns.summarise_run`/`note`/`report_fields`
      and `display` use it when every pair has a series.

## 2. Record
- [x] 2.1 Component (failing first): `tests/component/core/test_crypto_costs.py::
      TestPositionSeries` (real orders and `OrderFilled` events in a real `Cache`).
- [x] 2.2 `CryptoCommissionModel` numbers fills; `position_series`; `crypto_snapshot` saves it.
- [x] 2.4 Review fixes: fill numbering shared across fee models (cache count); a fill with no
      position id or a replay off the account balance by more than a cent a fill drops the
      series; each order's fills are listed once.
- [x] 2.3 Integration (`--forked`): `test_crypto_venue_engine.py::
      test_the_saved_position_series_replays_to_the_account_balance` (two hedged BTC positions,
      scale-in and partial cut; replay lands on the account balance).

## 3. Display
- [x] 3.1 `display.snapshot_params` in `backtest show` and the dashboard's parameters list;
      component test in `test_crypto_dashboard.py`; `FROZEN_VIEWS` for `backtest_detail.py`.

## 4. Check
- [x] 4.1 Smoke (real runs, see `docs/agent/crypto-backtesting.md`, "Exact peak exposure").
- [x] 4.2 Docs.

## 5. Store the computed peaks, not the series (follow-up, approved by Allay 2026-11-25)
- [x] 5.1 Unit (failing first): `test_exposure.py::TestStoredPeaks` (saved form, read back
      exactly, equal to the PR #79 series replay, neither keeps the upper bound).
- [x] 5.2 Component (failing first): `test_crypto_costs.py::TestPositionSeries` asserts no pair
      carries `position_series` and `crypto_peaks` holds the replayed peaks (only when the
      replay meets the account); `test_crypto_dashboard.py` shows saved peaks as exact.
- [x] 5.3 `crypto_costs.exact_peaks` + `crypto_snapshot` save `crypto_peaks`, declared on
      `StrategyConfigSnapshot` (undeclared keys are dropped on save; a component test pins
      every key `crypto_snapshot` emits against the model);
      `exposure.peaks_snapshot`/`stored_peaks`; `returns.summarise_run` reads saved peaks,
      else a PR #79 series, else the upper bound. No migration.
- [x] 5.4 Integration (`--forked`): the scale-in run saves the replayed peaks, no series.
- [x] 5.5 Smoke: SMA BTC 2023-2024 exact 10.3861% and the scripted scale-in 55,000 / 55.0165%
      unchanged.
