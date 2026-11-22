# Tasks

## 1. Cost models
- [x] 1.1 Component (failing first): `tests/component/core/test_futures_costs.py`:
      terms from the spec for all 5 roots, a bare futures contract errors, per-contract and
      price-independent commission, cost stress, equity fallback, dollar initial and
      maintenance margin at any price, default margin for other instruments,
      `venue_cost_models` leaves equity venues unchanged, `futures_snapshot`.
- [x] 1.2 Implement `src/core/futures_costs.py`.

## 2. Margin gate
- [x] 2.1 Integration, forked (failing first): `tests/integration/core/test_futures_margin_engine.py`:
      over-margin long and short entries denied, within margin fills, close never denied,
      P&L = points x 50 - commissions, equity venue has no gate and keeps per-share fees.
- [x] 2.2 Implement `src/core/futures_margin_gate.py` and `src/core/backtest_venue.py`.
- [x] 2.3 Wire `setup_backtest_venues` into `BacktestOrchestrator` (single and multi) and
      `MinimalBacktestRunner.run_backtest_with_catalog_data`; record `futures_snapshot` in
      `config_snapshot`.

## 3. Sizing
- [x] 3.1 Unit (failing first): helpers in `tests/unit/test_position_sizing.py`; futures
      sizing in `tests/unit/strategies/test_sma_crossover_position_sizing.py`, with the
      equity cases unchanged.
- [x] 3.2 Implement the helpers, `sma_crossover` and `buy_and_hold` sizing.
- [x] 3.3 Integration: orchestrator `sma_crossover` on the trimmed real ES 1-hour fixture
      trades whole contracts with per-contract fees and exact P&L.

## 4. Specs, docs, smoke
- [x] 4.1 Source note and as-of date per value in `contract_specs.yaml` (unverified values
      flagged).
- [x] 4.2 `docs/agent/futures-backtesting.md` (margin, ticks, sizing guide for external
      strategies, `profit_pct` semantics, stored fields); index in `architecture.md`.
- [x] 4.3 Manual smoke: `backtest run` ES 1-hour, about 3 months, persisted; one trade
      hand-checked; a short CL run; the run pages render 200 in-process (the port-3000
      dashboard was not running).
- [x] 4.5 Found by the smoke: `StrategyConfigSnapshot` drops undeclared keys, so declare
      `futures_contracts`/`profit_pct_basis` (omitted when unset; equity rows unchanged),
      test `tests/unit/models/test_config_snapshot_futures.py`; config-file YAML floats
      reached `SMACrossover` uncoerced (`float / Decimal`), coerced in `__init__`, test
      `tests/component/core/test_sma_config_coercion.py`.
- [x] 4.4 Size-cap baseline lowered for the shrunken entries.
