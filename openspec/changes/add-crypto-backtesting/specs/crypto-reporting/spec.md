## ADDED Requirements

### Requirement: Crypto runs report coin quantities, USD notional and costs
CLI `show`, `report` and `history`, the MCP `get_run`, `compare_runs` and `export_results`
tools, and the dashboard SHALL show for crypto runs the asset class `crypto`, fractional coin
quantities, USD notional, return on the underlying, USD P&L, the commission and modelled-spread
split with the fee tier, and the note "spot, unlevered, long-only". Equity, futures and
FX output SHALL be unchanged.

#### Scenario: CLI show of a BTC run
- **WHEN** `show` is run for a persisted BTC run with a 0.12820512 BTC trade
- **THEN** the crypto block lists `BTC/USD (crypto, KRAKEN, fee tier 1)`, quantity `0.12820512`, its USD
  notional and the costs split

#### Scenario: MCP payload is additive
- **WHEN** `get_run` is called for an equity run
- **THEN** the payload is identical to the one returned before this change

### Requirement: Crypto symbols resolve on the CLI
`backtest run` SHALL resolve `--symbol BTC`, `BTCUSD` or `BTC/USD` against a crypto catalog to
`BTC/USD.KRAKEN`, map `--timeframe` to a `LAST` bar type, and accept a decimal `--units` that is
a positive multiple of the size increment, and accept `--fee-tier N` for a tier in the
schedule.

#### Scenario: decimal units
- **WHEN** `backtest run --symbol BTC --units 0.25` is run against `firstrate-crypto`
- **THEN** each entry trades exactly 0.25 BTC

#### Scenario: invalid units
- **WHEN** `--units 0.000000001` is given for BTC
- **THEN** the command exits 2 naming the size increment
