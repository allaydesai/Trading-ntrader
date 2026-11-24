## ADDED Requirements

### Requirement: Spot crypto runs on an unlevered, long-only USD account
A backtest venue that lists a crypto `CurrencyPair` SHALL be a USD account in which an order is
denied `MARGIN_EXCEEDS_FREE_BALANCE` when its notional plus open exposure exceeds free USD, and
denied `CRYPTO_SPOT_NO_SHORT` when it would leave a net short position. Orders that only reduce
a long, including bracket exits, SHALL be accepted. Futures and FX deny reasons SHALL be
unchanged.

#### Scenario: short sale refused
- **WHEN** a strategy with no BTC position submits a 0.1 BTC SELL
- **THEN** the order is denied with a reason starting `CRYPTO_SPOT_NO_SHORT`

#### Scenario: bracket exits accepted
- **WHEN** a 0.1 BTC long carries a resting stop-loss and a take-profit SELL of 0.1 BTC each
- **THEN** both exits are accepted and the one that fills closes the position

#### Scenario: a close cannot strand a resting sell
- **WHEN** a 0.1 BTC long has a resting 0.1 BTC limit SELL and the strategy closes the long at
  market
- **THEN** the close is denied with a reason starting `CRYPTO_SPOT_NO_SHORT` and the long stays
  open

#### Scenario: no leverage
- **WHEN** an account with USD 10,000 free submits a BUY of 0.2 BTC at 78,000
- **THEN** the order is denied with a reason starting `MARGIN_EXCEEDS_FREE_BALANCE`

### Requirement: Crypto commission follows the Kraken Pro spot schedule, in USD
A crypto venue SHALL charge each fill, in USD and scaled by the cost-stress multiplier, the
run's Kraken Pro fee tier rate times the fill's trade value, using the maker rate for a fill
that rested on the book and the taker rate otherwise, plus a modelled half-spread per side.
No minimum fee SHALL apply. Equity, futures and FX venues SHALL keep their models unchanged.

#### Scenario: tier 1 taker
- **WHEN** 0.128 BTC is bought at 78,000 by a market order at tier 1, with no spread
- **THEN** the commission is USD 79.87

#### Scenario: tier 1 maker
- **WHEN** the same quantity fills from a resting limit order at tier 1, with no spread
- **THEN** the commission is USD 39.94

#### Scenario: default tier
- **WHEN** the run is started without a fee tier and the market order above fills
- **THEN** the commission is USD 59.90 (tier 2 taker, the spec's default) and the snapshot
  records tier 2

#### Scenario: selected tier
- **WHEN** the run is started with fee tier 4 and the market order above fills
- **THEN** the commission is USD 34.94 and the snapshot records tier 4

### Requirement: Crypto positions are sized in fractional coin units
Sizing helpers and strategies SHALL size crypto orders in base-coin units floored to the
instrument's size increment, with no minimum of one unit, submit them through the instrument's
quantity factory, and drop (and count once per run) an order below the venue minimum.

#### Scenario: notional budget on BTC
- **WHEN** a USD 10,000 budget is sized on BTC at 78,000 with increment 0.00000001
- **THEN** the order quantity is 0.12820512 BTC

### Requirement: Crypto risk metrics annualise on calendar days
A run whose instruments are crypto SHALL annualise volatility, Sharpe and Sortino on 365
periods per year and record the value in its config snapshot. Other runs SHALL keep 252.

#### Scenario: snapshot records the basis
- **WHEN** a BTC run is persisted
- **THEN** its config snapshot has `periods_per_year` 365 and an equity run's snapshot has no
  such key

### Requirement: Kraken-fetched data is never priced from perpetuals
A backtest with `--data-source kraken` SHALL fetch a spot price series for a spot pair, or
refuse the request with a message naming the unsupported range, and SHALL build its instrument
and fees from the coin spec when the coin has a spec.

#### Scenario: BTC/USD from Kraken
- **WHEN** `backtest run --data-source kraken --symbol BTC/USD` fetches bars
- **THEN** no perpetual (`PF_`) chart symbol is requested and the venue charges the Kraken Pro
  spot schedule
