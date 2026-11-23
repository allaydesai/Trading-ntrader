# fx-reporting Specification

## Purpose
How FX runs are reported (pips, lots, USD notional, leverage, cost split) and how `backtest run` and
the MCP submissions resolve FX symbols, bar types and unit overrides. Created by archiving change
`add-fx-backtesting` (`openspec/changes/archive/2026-11-23-add-fx-backtesting/`).

## Requirements
### Requirement: FX runs report pips, lots, USD notional, leverage and costs
For a run whose `config_snapshot` has `fx_pairs`, `show`, `report`, `history`, the MCP run views
and export, and the dashboard SHALL report per trade units, lots, USD notional, side-aware pips,
side-aware return on the underlying and USD P&L. Per run they SHALL report peak notional
leverage, commission versus modelled spread, a "financing not modelled" note, and the sum of
closed trades' USD P&L labelled "Closed-trade P&L" (`closed_trade_pnl_usd`), never "Net P&L". The account
return on USD equity SHALL stay the headline. Equity and futures output SHALL be unchanged
apart from MCP's additive `asset_class`.

#### Scenario: USDJPY short
- **WHEN** a 100,000 USDJPY short enters at 151.000 and exits at 150.500
- **THEN** it reports +50.0 pips, 1.00 lots and USD notional 100,000

### Requirement: `backtest run` resolves FX symbols, bar types and units
`backtest run` SHALL accept `EURUSD` or `EUR/USD` for an FX catalog row, SHALL load `MID` bars
for it from `--timeframe`, and SHALL accept `--units N` (N >= 1) only for FX instruments and
strategies whose config declares `units` (usage error, exit 2, otherwise).

#### Scenario: equity with units
- **WHEN** `--units 1000` is given for AAPL
- **THEN** the command exits 2 with a message that `--units` is for FX only

### Requirement: MCP backtest submissions accept `units` and `contracts`
The research MCP's `submit_backtest`, `validate_config` and a study's in-sample submit SHALL
accept optional `units` (FX) and `contracts` (futures) with the CLI's `--units`/`--contracts`
rules: a positive whole number, a strategy whose config declares the setting, `units` only for
an FX catalog row and `contracts` only for a futures root. A refused value SHALL queue nothing.
Unset, the resolved request and its config hash SHALL be unchanged.

#### Scenario: units on an equity
- **WHEN** `submit_backtest` is called for AAPL with `units` 100000
- **THEN** it returns `validation_failed` with an `invalid_params` error saying units are for
  FX only, and no job is queued

#### Scenario: contracts on ES
- **WHEN** `submit_backtest` is called for ES with `contracts` 2
- **THEN** the resolved parameters and the stored spec carry `contracts` 2
