## ADDED Requirements

### Requirement: FX runs report pips, lots, USD notional, leverage and costs
For a run whose `config_snapshot` has `fx_pairs`, `show`, `report`, `history`, the MCP run views
and export, and the dashboard SHALL report per trade units, lots, USD notional, side-aware pips,
side-aware return on the underlying and USD P&L. Per run they SHALL report peak notional
leverage, commission versus modelled spread, and a "financing not modelled" note. The account
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
