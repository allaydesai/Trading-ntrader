## ADDED Requirements

### Requirement: Return on margin is the futures headline
For a run whose `config_snapshot` has `futures_contracts`, every report SHALL show return on
margin, `net P&L / peak concurrent initial margin x 100`, as its headline return, next to
the compounded side-aware return on the underlying. A run without `futures_contracts` SHALL
be reported exactly as before.

#### Scenario: ES, two trades
- **WHEN** an ES run (multiplier 50, initial margin 27,500 USD, 2.25 USD per side) has a
  2-lot long from 5000 to 5020 (P&L 1,991 USD) and then a 1-lot short from 5030 to 5040
  (P&L -504.50 USD)
- **THEN** peak margin is 55,000 USD, return on margin is 2.70% (1,486.50 / 55,000) and the
  return on the underlying is 0.20% ((1.004 x (1 - 10/5030)) - 1)

#### Scenario: equity run
- **WHEN** a run has no `futures_contracts`
- **THEN** the futures summary is `None` and the CLI, report and MCP output are unchanged
  apart from MCP's additive `asset_class: "equity"`

### Requirement: Futures trades show contracts, points and percentages
Each futures trade SHALL report contracts, multiplier, notional
(`quantity x entry x multiplier`), margin used (`quantity x initial margin`), adjusted points,
side-aware return on the underlying and return on margin. Dollar P&L and points SHALL be
labelled ratio-adjusted, with the ratio-adjustment note.

#### Scenario: winning short
- **WHEN** a 1-lot ES short enters at 5040 and exits at 5030
- **THEN** points are +10 and the return on the underlying is positive

### Requirement: `backtest run --contracts N` trades whole contracts
`backtest run` SHALL accept `--contracts N` (N >= 1) and trade exactly N contracts per entry
for a futures instrument. It SHALL be a usage error (exit 2) for N < 1, for a strategy whose
config has no `contracts` setting, and for a non-futures instrument. Without the flag, sizing
SHALL be unchanged.

#### Scenario: ES with one contract
- **WHEN** `backtest run --symbol ES --catalog firstrate-futures --timeframe 1-HOUR
  --contracts 1` runs `sma_crossover`
- **THEN** every entry is 1 contract and the run places trades

#### Scenario: equity with contracts
- **WHEN** `--contracts 2` is given for AAPL
- **THEN** the command exits 2 with a message that `--contracts` is for futures only
