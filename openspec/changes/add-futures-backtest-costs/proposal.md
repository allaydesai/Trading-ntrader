# Proposal: futures margin, commission and sizing in backtests (M3)

## Why

M1 built `FuturesContract` instruments and M2 filled the `firstrate-futures` catalog, but a
futures backtest is still costed and sized like an equity one:

- Commission is the IBKR per-share model ($0.005/unit, $1 minimum, 0.5% cap), which ignores
  the multiplier and charges a $350k ES contract like one share.
- Leverage is unlimited. Nautilus 1.220 computes margin on a margin account but never
  refuses an order over it, and the instruments carry no margin rates anyway.
- `sma_crossover` and the `buy_and_hold` benchmark size as `budget / price`, so $100k buys
  about 14 ES "shares", which are really contracts worth about $5M.

## What changes

- `src/core/futures_costs.py`:
  - `FuturesCommissionModel`, a spec per-contract, per-side commission scaled by the run's
    cost multiplier.
  - `FuturesMarginModel`, a Nautilus `MarginModel` charging spec dollars per contract
    (initial for orders, maintenance for positions) at any price.
  - `venue_cost_models` picks the models by instrument type.
  - `futures_snapshot` records the terms a run used.
- `src/core/futures_margin_gate.py`: `FuturesMarginGate` wraps the `RiskEngine.execute`
  message-bus endpoint and denies, with Nautilus' own `OrderDenied`, any futures order whose
  new exposure needs more initial margin than the account has free.
- `src/core/backtest_venue.py`: `setup_backtest_venues` is the one venue setup the
  orchestrator (single and multi-instrument) and the legacy runner's catalog path use.
- `src/core/position_sizing.py`:
  - framework-free helpers `contract_notional`, `units_for_notional`, `whole_contracts` and
    `instrument_multiplier`;
  - `calculate_risk_based_size` takes an optional `multiplier`;
  - `sma_crossover` and `buy_and_hold` size by notional and use whole contracts.
- Futures runs add `futures_contracts` and `profit_pct_basis` to `config_snapshot`. Both are
  declared on `StrategyConfigSnapshot`, which persistence uses and which drops undeclared
  keys; they are omitted from the dump when unset.
- `SMACrossover` coerces `portfolio_value`/`position_size_pct` to `Decimal`: config-file
  runs passed YAML floats through and crashed on the first signal (an existing bug the smoke
  run hit).
- `contract_specs.yaml` carries a source note and as-of date for each verified or unverified
  value.
- Docs: `docs/agent/futures-backtesting.md`, which also guides external strategies.

## Interfaces crossed

- **Record shape**: a futures run's `backtest_runs.config_snapshot` gains
  `futures_contracts` (per instrument: multiplier, dollar margins, commission, margin as-of)
  and `profit_pct_basis: "underlying"`. Equity runs are unchanged.
- **Order events**: futures orders over margin now end `DENIED` with reason prefix
  `MARGIN_EXCEEDS_FREE_BALANCE`. A modify that would raise a futures order's quantity is
  rejected (`OrderModifyRejected`, reason prefix `FUTURES_QTY_INCREASE_NOT_SUPPORTED`):
  cancel and resubmit instead.
- **Sizing**: `SMACrossover._calculate_position_size` may return `None` for a multiplied
  instrument, meaning no order. Equity and crypto results are byte-identical.

No CLI flag, HTTP route, MCP contract, DB column or migration changes (`3f56b46c78eb` stays
the single head).

## Guard lists / pinned constants

- No `src/core/live_*.py`, `src/cli/commands/live*.py`, `src/api/**` or `templates/**` file is
  touched, so no live guard list or `FROZEN_VIEWS` entry applies.
- `sma_crossover.py` is in the globbed `STRATEGY_MODULES`; its warm-up shape scan still
  passes.
- Size caps shrink only. `BacktestOrchestrator` goes 278 to 277,
  `MinimalBacktestRunner` 902 to 901, `run_backtest_with_catalog_data` 119 to 118 and
  `SMACrossover` 106 to 101; the baseline entries are lowered to match.

## Out of scope

- Dashboard display of points, notional and return on margin: M4. This change only stores
  what M4 needs.
- Margin calls and liquidation, unrealised-P&L-aware buying power, intraday margins.
- Rounding fills or stops to the exchange tick: deliberately not done (see design).
- Changing the external strategies in the `custom/` submodule (guide only).
- Live/IB futures (M5).
