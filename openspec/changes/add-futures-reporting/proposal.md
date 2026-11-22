# Proposal: futures results in reports, CLI and dashboard (M4)

## Why

M3 costs, margins and sizes futures backtests and stores the contract terms each run used
(`config_snapshot.futures_contracts`, `profit_pct_basis`). Nothing reads them yet, so a
futures run is reported like an equity one:

- the headline return is on the whole account's cash, which says little about a strategy
  that ties up `contracts x initial margin`;
- dollar P&L of old trades is scaled by FirstRate's ratio adjustment (CL 2008 is about 3.7x);
- `backtest run` sizes with `position_size_pct` of a notional budget, so a research run
  can't say "trade 1 ES contract", and small budgets size ES to 0 contracts (no trades).

Approved decisions (Allay):

- Return on margin, `P&L / (contracts x initial margin)`, is the headline metric for futures
  runs, shown next to the return on the underlying price.
- Historical trade P&L for futures is shown as a percentage of the underlying; stored dollar
  values are kept, labelled as ratio-adjusted, with a short explanation.
- Futures get a dedicated CLI size option (`--contracts N`); equity CLI behaviour is unchanged.

## What changes

- `src/services/futures/returns.py` (new, framework-free): reads a run's snapshot and trade
  rows into per-trade figures (contracts, multiplier, notional, margin used, adjusted points,
  side-aware return on the underlying, return on margin) and a run summary (asset class, peak
  concurrent margin, net P&L, return on margin, compounded return on the underlying).
  `None` for equity runs. Plain-dict `report_fields`/`trade_fields` serialise them.
- `src/services/futures/display.py` (new): text rows and the ratio-adjustment note shared by
  the CLI renderers, so `show`, `report` and `history` word the figures the same way.
- CLI:
  - `backtest run --contracts N` (N >= 1): trade exactly N whole contracts. Validated: a
    strategy without a `contracts` setting, or a non-futures instrument, is a usage error
    (exit 2). Config files can set `contracts:` in the strategy `config:` section; the flag
    overrides it.
  - `SMAConfig.contracts: int | None = None`. Unset, sizing is exactly as before.
  - `backtest show`: a Futures panel (terms, headline return on margin, return on the
    underlying, peak margin) after the metrics.
  - `report summary|generate|export-all`: text, CSV and JSON carry the futures fields;
    the JSON adds a `futures` object (run summary) and `trades` with per-trade figures.
  - `history`: a futures run's symbol reads `ES (fut)` and its Return column shows
    return on margin (`RoM +2.70%`). Equity rows unchanged.
- MCP (research server):
  - `get_run`/`compare_runs` rows gain `asset_class` and, for futures runs, a `futures`
    object (the run summary).
  - `export_results` trade CSV gains futures columns only when an exported run is futures.
    The run JSON gains `futures` for futures runs.
- Docs: `docs/agent/futures-backtesting.md` (reporting section, `--contracts`), README.

## Interfaces crossed

- **CLI flag**: `backtest run --contracts N` (new). No exit code changes; a misuse is the
  usual click usage error (exit 2).
- **Strategy config**: `SMAConfig` gains optional `contracts`.
- **MCP tool contract**: `get_run`, `compare_runs` and `export_results` output gain fields
  (additive; equity rows only gain `asset_class: "equity"`).
- **Report formats**: `report generate --format csv|json` gain fields for futures runs only.
- No DB column, migration, HTTP route or live-path change. `3f56b46c78eb` stays the head.

## Guard lists / pinned constants

- **`FROZEN_VIEWS` blocks the dashboard part.** Every dashboard route, view model and
  template is under `src/api/**` or `templates/**`, which `FROZEN_VIEWS` pins byte for byte
  (an added file there fails it too), and the run list/comparison read
  `src/services/backtest_query.py`. Per the task's rule and the guard's own text, this change
  **does not edit them**. It builds the futures figures where the dashboard can consume them
  unchanged, and `design.md` lists the exact frozen edits needed, for a PO ruling.
- No `src/core/live_*.py` or `src/cli/commands/live*.py` file is touched.
- `sma_crossover.py` is in the globbed `STRATEGY_MODULES`; no list edit.
- Size caps: `SMACrossover` (baseline 101) and `_list_history_sync` (106) must not grow; new
  logic goes into module-level helpers.

## Out of scope

- The dashboard edits themselves (blocked on the PO ruling above).
- Real (unadjusted) points: needs the adjustment factor or unadjusted prices.
- Return on margin for the equity curve or risk ratios (Sharpe and drawdown stay on the
  account's capital).
- Porting the `custom/` submodule strategies to `--contracts`.
- Live/IB futures (M5).
