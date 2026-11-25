## ADDED Requirements

### Requirement: Crypto runs report coin quantities, USD notional and costs
CLI `show`, `report` and `history`, the MCP `get_run`, `compare_runs` and `export_results`
tools, and the dashboard SHALL show for crypto runs the asset class `crypto`, fractional coin
quantities, USD notional, return on the underlying, USD P&L, the commission and modelled-spread
split with the fee tier, and the note "spot, unlevered, long-only". Equity, futures and
FX output SHALL be unchanged.

#### Scenario: CLI show of a BTC run
- **WHEN** `show` is run for a persisted BTC run, started without a fee tier (the default,
  tier 2), with a 0.12820512 BTC trade
- **THEN** the crypto block lists `BTC/USD (crypto, KRAKEN, fee tier 2)` (the run's tier), quantity `0.12820512`, its USD
  notional and the costs split

#### Scenario: Orders dropped under the minimum are saved with the run
- **WHEN** a crypto run drops 37 orders under Kraken's minimum order
- **THEN** its `config_snapshot.crypto_pairs["BTC/USD.KRAKEN"].orders_below_minimum` is 37 and
  `show`, MCP `get_run` and the dashboard report 37; a run saved before this field reports
  "not recorded", not 0

#### Scenario: MCP payload is additive
- **WHEN** `get_run` is called for an equity run
- **THEN** the payload is identical to the one returned before this change

### Requirement: Crypto symbol forms name one coin
`backtest run`, the MCP backtest request and the dashboard Run form SHALL resolve `BTC`,
`BTCUSD`, `BTC/USD`, `XBTUSD` and `BTC/USD.KRAKEN` against a crypto catalog to the same coin
(`BTC`, the `BTC/USD.KRAKEN` row), and SHALL refuse a crypto-shaped symbol no spec ships,
naming the shipped pairs.

#### Scenario: symbol forms
- **WHEN** `backtest run --symbol XBTUSD --units 0.25 --fee-tier 3` is run against
  `firstrate-crypto`
- **THEN** it is the same run (config hash) as `--symbol BTC --units 0.25 --fee-tier 3`

#### Scenario: unshipped pair
- **WHEN** `--symbol BTC/EUR` is given against `firstrate-crypto`
- **THEN** the command exits 2 naming the shipped pairs BTC/USD, ETH/USD, SOL/USD

### Requirement: Crypto symbols resolve on the CLI
`backtest run` SHALL map `--timeframe` to a `LAST` bar type for a crypto coin, accept a decimal
`--units` that is a positive multiple of the size increment, accept `--fee-tier N` for a tier
in the schedule, and accept `--spread-bps X` replacing the coin's modelled spread.

#### Scenario: decimal units
- **WHEN** `backtest run --symbol BTC --units 0.25` is run against `firstrate-crypto`
- **THEN** each entry trades exactly 0.25 BTC

#### Scenario: invalid units
- **WHEN** `--units 0.000000001` is given for BTC
- **THEN** the command exits 2 naming the size increment

#### Scenario: invalid fee tier
- **WHEN** `--fee-tier 18` is given for BTC
- **THEN** the command exits 2 naming the schedule's tiers 1..17
