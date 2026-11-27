# Spec Delta

## Purpose

The `donchian_breakout` strategy: a daily Donchian channel breakout with an ATR stop and
risk-based sizing that trades stocks, ETFs, futures, spot FX and spot crypto, with shorts
allowed only where its configuration turns them on and the venue permits them.

## ADDED Requirements

### Requirement: Registered strategy
The strategy SHALL be registered as `donchian_breakout` with its configuration, a validated
parameter model and a default configuration, so the CLI, the research server and the web UI
list and describe it.

#### Scenario: listed and described
- **WHEN** `list_strategies` and `describe_strategy("donchian_breakout")` are called on the
  research server
- **THEN** the strategy is listed and its parameters `entry_lookback`, `exit_lookback`,
  `atr_period`, `stop_atr_mult`, `max_risk_pct`, `max_position_pct`, `allow_short`,
  `portfolio_value`, `contracts` and `units` are described with their defaults

#### Scenario: exit channel must be shorter than the entry channel
- **WHEN** a configuration sets `exit_lookback` greater than or equal to `entry_lookback`
- **THEN** validation refuses it and names both parameters

### Requirement: Breakout entry on the signal bar's close
When flat, the strategy SHALL enter long when a bar closes above the highest high of the
`entry_lookback` bars before it, and enter short when a bar closes below the lowest low of
those bars only if `allow_short` is on. The signal bar's own high and low SHALL NOT be part of
the channel. The entry SHALL be a market order sent on the signal bar.

#### Scenario: long breakout
- **WHEN** the strategy is flat, `entry_lookback` is 20 and a bar closes above the highest high
  of the previous 20 bars
- **THEN** it submits one market BUY on that bar, which fills at that bar's close

#### Scenario: no signal until the channel is full
- **WHEN** fewer than `entry_lookback` bars, or fewer than `atr_period` bars, have been seen
- **THEN** no order is submitted

#### Scenario: short breakout with shorts off
- **WHEN** the strategy is flat, `allow_short` is off and a bar closes below the lowest low of
  the previous `entry_lookback` bars
- **THEN** no order is submitted

#### Scenario: short breakout with shorts on
- **WHEN** the strategy is flat on a futures contract or an FX pair, `allow_short` is on and a
  bar closes below the lowest low of the previous `entry_lookback` bars
- **THEN** it submits one market SELL on that bar

### Requirement: Spot crypto never shorts
On a spot crypto pair the strategy SHALL take no short entry even when `allow_short` is on, and
SHALL log that shorts are disabled once per run.

#### Scenario: crypto short signal ignored
- **WHEN** `allow_short` is on, the instrument is BTC/USD and a short breakout occurs
- **THEN** no SELL is submitted and the run has no `CRYPTO_SPOT_NO_SHORT` denial from this
  strategy

### Requirement: Channel and stop exits on the close
An open position SHALL be closed with a market order on the first bar that closes through the
`exit_lookback` channel against it, or through its stop, which is `stop_atr_mult` x ATR from
the entry bar's close, fixed at entry. The strategy SHALL keep no resting stop order. A bar
that closes a position SHALL NOT also open one.

#### Scenario: long channel exit
- **WHEN** a long is open, `exit_lookback` is 10 and a bar closes below the lowest low of the
  previous 10 bars
- **THEN** the strategy closes the long with a market order on that bar

#### Scenario: long stop exit
- **WHEN** a long entered at 100.00 with ATR 2.00 and `stop_atr_mult` 2 is open and a bar
  closes at 95.90
- **THEN** the strategy closes the long on that bar, even if the exit channel is not broken

#### Scenario: short exits mirror long exits
- **WHEN** a short is open and a bar closes above the highest high of the previous
  `exit_lookback` bars, or above entry plus `stop_atr_mult` x ATR
- **THEN** the strategy closes the short with a market order on that bar

#### Scenario: no reversal on the exit bar
- **WHEN** a bar closes a long and the same bar is also a short breakout
- **THEN** only the close is submitted on that bar; a new entry can come on a later bar

### Requirement: Risk-based sizing with a notional cap
An entry SHALL be sized so a stop exit loses `max_risk_pct` of `portfolio_value`, in the
instrument's own unit (whole shares, whole futures contracts, FX base units, fractional coins
floored to the size increment), and SHALL NOT exceed `max_position_pct` of `portfolio_value`
in USD notional. A size of zero SHALL submit no order. `contracts` (futures) or `units` (FX,
crypto) SHALL trade exactly that size instead.

#### Scenario: equity risk size
- **WHEN** `portfolio_value` is 1,000,000, `max_risk_pct` is 1, the close is 100.00 and the
  stop distance is 4.00
- **THEN** the entry is 2,500 shares

#### Scenario: notional cap binds
- **WHEN** the risk size would be worth more than `max_position_pct` of `portfolio_value`
- **THEN** the entry is reduced to the largest size within that notional

#### Scenario: futures floor to whole contracts
- **WHEN** the risk size on a futures contract is under one contract
- **THEN** no order is submitted

#### Scenario: fixed contracts
- **WHEN** `contracts` is 2 on a futures contract
- **THEN** every entry is exactly 2 contracts

### Requirement: Lifecycle hooks place no orders
The strategy's stop and dispose hooks SHALL only tear down subscriptions; they SHALL NOT submit,
cancel or close anything.

#### Scenario: open position at the end of a run
- **WHEN** a run ends with a position open
- **THEN** the strategy submits no closing order and the position is reported open
