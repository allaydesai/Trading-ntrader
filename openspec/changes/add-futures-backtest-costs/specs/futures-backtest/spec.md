## ADDED Requirements

### Requirement: Futures commission is per contract per side
A backtest venue listing a futures contract SHALL charge each fill
`fill quantity x commission_per_contract_usd x cost multiplier`, from the contract's spec.
An equity-only venue SHALL keep the IBKR per-share model unchanged.

#### Scenario: ES round trip
- **WHEN** one ES contract is bought and sold
- **THEN** total commission is 2 x 2.25 USD, and the realised P&L is points x 50 - 4.50 USD

#### Scenario: equity venue
- **WHEN** a venue lists only equities
- **THEN** its fee model is `IBKRCommissionModel` and no margin model is passed

### Requirement: Futures margin is dollars per contract and enforced
A futures venue SHALL compute initial margin as `contracts x margin.initial_usd` and
maintenance margin as `contracts x margin.maintenance_usd`, at any price. An order SHALL be
denied (`OrderDenied`, reason prefix `MARGIN_EXCEEDS_FREE_BALANCE`) when its new-exposure
contracts x initial margin exceed the account's free balance. Reduce-only orders SHALL never
be denied for margin.

#### Scenario: over margin
- **WHEN** a $50,000 account submits a 2-lot ES buy ($55,000 initial)
- **THEN** the order is denied and no position opens

#### Scenario: within margin, then over with a position on
- **WHEN** the same account buys 1 ES (filled), then submits 1 more
- **THEN** the second is denied (free = 50,000 - 25,000 maintenance - fees < 27,500)

#### Scenario: raising a working order
- **WHEN** a $50,000 account rests a 1-lot ES limit and modifies it to 3 lots
- **THEN** the modify is rejected (`OrderModifyRejected`) and the order stays at 1 lot

#### Scenario: an order list spends the closing credit once
- **WHEN** a list of two 1-lot sells targets a 1-lot long and free margin is below one lot's
- **THEN** the list is denied (the second sell would open a short)

#### Scenario: equities unaffected
- **WHEN** an equity run submits an order far above its cash
- **THEN** no margin gate is installed and the order fills as before

### Requirement: Sizing is multiplier-aware in whole contracts
`sma_crossover` and `buy_and_hold` SHALL size futures as
`floor(budget / (price x multiplier))` contracts and SHALL place no order when that is 0.
Equity sizing SHALL be unchanged.

#### Scenario: $100k on ES
- **WHEN** `sma_crossover` sizes $100,000 at ES 7000
- **THEN** it places no order (0.29 contracts), not about 14

### Requirement: Futures runs record their contract terms
A persisted futures run SHALL store `config_snapshot.futures_contracts` (per instrument:
multiplier, initial/maintenance margin USD, commission per contract, margin as-of) and
`config_snapshot.profit_pct_basis = "underlying"`. Equity runs SHALL store neither key.

#### Scenario: ES run
- **WHEN** an ES backtest is persisted
- **THEN** `config_snapshot.futures_contracts["ES.CME"].multiplier` is `"50"`
