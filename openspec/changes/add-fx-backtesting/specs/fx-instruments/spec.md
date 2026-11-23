## ADDED Requirements

### Requirement: FX pair specs are hand-curated, versioned and validated
The system SHALL load spot FX pair specs from a versioned YAML file holding, per pair, base
and quote currency, venue (`IDEALPRO`), pip size, price precision, IB minimum tick, size
increment, IDEALPRO minimum order (USD), typical spread in pips, dated initial and maintenance
margin percentages, the IB commission schedule and the IB fields `sec_type: CASH`, `symbol`,
`currency`, `exchange`, `local_symbol`. It SHALL reject an invalid file with `FxSpecError`
naming the file and the dotted field path.

#### Scenario: shipped file
- **WHEN** the shipped spec file is loaded
- **THEN** EURUSD, GBPUSD, USDJPY and EURGBP are returned, EURUSD with base EUR, quote USD,
  pip size 0.0001 and precision 5, and USDJPY with pip size 0.01 and precision 3

#### Scenario: inconsistent pair
- **WHEN** a pair keyed `EURUSD` declares `base: GBP`, or its `ib.currency` differs from its quote
- **THEN** loading raises `FxSpecError` naming the field

### Requirement: Spot FX instruments are IB-compatible currency pairs
The system SHALL build a Nautilus `CurrencyPair` for an FX catalog row with id
`{BASE}/{QUOTE}.IDEALPRO`, `raw_symbol` `{BASE}.{QUOTE}`, the spec's currencies, price
precision taken from the bars (or the spec), whole-unit size increment, and margin fractions
from the spec percentages.

#### Scenario: build EURUSD
- **WHEN** `build_instrument("FX", "EUR/USD.IDEALPRO", "EURUSD", bars)` is called with
  5-decimal bars
- **THEN** it returns a `CurrencyPair` with base EUR, quote USD, price precision 5, and an id
  that the IB adapter's CASH symbology parses as `EUR`/`USD`

#### Scenario: other asset classes unchanged
- **WHEN** a STOCK, ETF, FUTURES or unset row is loaded
- **THEN** the instrument equals what `build_instrument` returned before this change

### Requirement: A USD account converts non-USD P&L
A backtest venue that lists a `CurrencyPair` SHALL be a single-currency USD margin account.
The run SHALL keep an exchange rate for every quote currency it trades, and SHALL fail rather
than complete if a fill happens without one. A cross whose conversion pair's bars start after or
end before the traded pair's bars in the window SHALL be refused before the engine starts (and,
through the MCP, before it is queued), naming the pair and the dates it covers.

#### Scenario: USDJPY round trip
- **WHEN** 100,000 USDJPY is bought at 150.000 and sold at 151.000 on a USD account
- **THEN** the USD balance changes by 100,000 JPY converted at about 151.000 (about USD 662.25)
  before costs, not by 100,000

#### Scenario: missing conversion pair
- **WHEN** an EURGBP run's catalog has no GBPUSD bars
- **THEN** the run fails before the engine starts, naming GBPUSD

#### Scenario: conversion pair starts late
- **WHEN** an EURGBP run trades from 2024-01-02 and the catalog's GBPUSD starts 2024-01-15
- **THEN** the run is refused before the engine starts with a message naming GBPUSD, the dates
  it covers and the dates EURGBP trades
