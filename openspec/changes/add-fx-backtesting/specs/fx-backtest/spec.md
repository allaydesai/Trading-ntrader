## ADDED Requirements

### Requirement: FX commission follows IB's spot schedule, in USD
A venue listing a `CurrencyPair` SHALL charge each order
`max(rate_bps / 10,000 x trade value in USD, min_per_order_usd) x cost multiplier` in USD,
applying the minimum once per order across partial fills. Equity and futures venues SHALL keep
their models unchanged.

#### Scenario: EURUSD 100k
- **WHEN** 100,000 EURUSD is bought at 1.10000 with 0.20 bp and a USD 2.00 minimum
- **THEN** the commission is USD 2.20

#### Scenario: small order
- **WHEN** 10,000 EURUSD is bought at 1.10000
- **THEN** the commission is the USD 2.00 minimum

### Requirement: The FX spread is modelled as a per-fill cost
Each FX fill SHALL be charged `quantity x typical_spread_pips x pip_size / 2` in the quote
currency, converted to USD and scaled by the cost multiplier, recorded separately from the
commission.

#### Scenario: EURUSD half spread
- **WHEN** 100,000 EURUSD fills with a 0.5-pip typical spread
- **THEN** the fill's spread cost is USD 2.50

### Requirement: FX margin is a percentage of notional and enforced
An FX venue SHALL compute initial margin as `notional in USD x margin_pct`. An order whose new
exposure needs more initial margin than the account has free SHALL be denied with reason
prefix `MARGIN_EXCEEDS_FREE_BALANCE`. Reduce-only orders SHALL never be denied for margin.
Every futures margin rule and reason SHALL behave as before.

#### Scenario: over margin
- **WHEN** a USD 10,000 account at 3% margin submits a 400,000 EURUSD buy at 1.10000
  (initial margin USD 13,200)
- **THEN** the order is denied and no position opens

#### Scenario: a pending reprice holds its increase
- **WHEN** a working 100,000 EURUSD limit at 1.10000 with USD 100 free is repriced to 1.12000
  (USD 60 more) and the venue has not applied it yet
- **THEN** a new entry or another order's reprice is checked against USD 40 free, and once the
  modify is applied, rejected or the order closes, the hold is released

#### Scenario: an applied modify on a trailing entry
- **WHEN** a parentless trailing stop entry's modify has been applied by the venue
- **THEN** later FX entries on the venue are checked against free margin as usual, not refused
  `FX_MODIFY_PENDING`

#### Scenario: a resting entry that cannot be priced
- **WHEN** an EURGBP limit is submitted before GBPUSD has a bar (no USD rate)
- **THEN** it is denied with reason prefix `FX_MARGIN_UNPRICEABLE`

### Requirement: FX sizing is in base-currency units with USD budgets
Strategy sizing helpers SHALL size FX as `floor(budget_usd / (price x quote-to-USD rate))` base
units (risk sizing: `risk_usd / (stop distance x quote-to-USD rate)`), floored to the size
increment, with no minimum of one unit. `--units N` SHALL trade exactly N units.

#### Scenario: USDJPY notional
- **WHEN** `sma_crossover` sizes USD 100,000 of USDJPY at 157.000
- **THEN** it orders 100,000 units, not 636

### Requirement: FX runs record their terms
A persisted FX run SHALL store `config_snapshot.account_currency = "USD"` and
`config_snapshot.fx_pairs` (per instrument: base, quote, pip size, margin %, commission
terms, spread, as-of, conversion pair), and SHALL store each trade's `profit_loss` in USD.
Equity and futures runs SHALL store neither key.

#### Scenario: USDJPY trade row
- **WHEN** a USDJPY trade closes with P&L 100,000 JPY at 151.000
- **THEN** its `profit_loss` is about 662.25 (USD)

### Requirement: Riptide and Bollinger Reversal trade FX at instrument precision
Riptide and Bollinger Reversal SHALL build order prices with the instrument's precision, size
FX through the FX helpers, and bucket Bollinger's weekly SMA by New York trade date. Riptide
SHALL skip its dollar-volume filter for FX. Their equity and futures behaviour SHALL be
unchanged.

#### Scenario: Bollinger bracket on EURUSD
- **WHEN** Bollinger Reversal enters EURUSD with a stop at 1.084314
- **THEN** the stop is submitted as 1.08431 and the bracket is not denied
