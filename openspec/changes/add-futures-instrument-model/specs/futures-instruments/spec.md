## ADDED Requirements

### Requirement: Futures contract specs are hand-curated, versioned and validated
The system SHALL load futures contract specs from a versioned YAML file holding, per root,
venue, multiplier, tick size, price precision, currency, ET trading session, dated
approximate initial/maintenance margin, per-contract commission, underlying and the IB
fields `sec_type`, `exchange`, `trading_class`, `currency`, `multiplier`, and SHALL reject
an invalid file with an error naming the file and field.

#### Scenario: shipped file covers the five roots
- **WHEN** the shipped spec file is loaded
- **THEN** specs for ES, NQ, CL, GC and ZN are returned with their venues CME, CME, NYMEX,
  COMEX, CBOT

#### Scenario: invalid spec
- **WHEN** a spec file has an unknown key, an inconsistent IB block, or initial margin
  below maintenance
- **THEN** loading raises `FuturesSpecError` whose message names the dotted field path

### Requirement: Continuous futures instruments
The system SHALL build a Nautilus `FuturesContract` for a continuous series with id
`{ROOT}.{VENUE}`, the spec's multiplier and currency, an activation/expiration window that
never rejects orders on historical data, and `info.contract_kind = "continuous"`.

#### Scenario: build ES
- **WHEN** `build_futures_contract` is called for ES
- **THEN** the instrument id is `ES.CME`, multiplier 50, and expiration is 2100-01-01 UTC

### Requirement: Instrument dispatch by asset class
The backtest loader SHALL build instruments through `build_instrument`, routing `FUTURES`
rows to the futures builder and every other asset class to the unchanged equity builder.

#### Scenario: equity unchanged
- **WHEN** a STOCK or ETF row is loaded
- **THEN** the instrument equals what `build_equity` returned before this change

### Requirement: Catalog instruments carry futures fields
`catalog_instruments` SHALL have nullable `multiplier`, `tick_size`, `currency`,
`contract_kind` (`continuous` or `specific`), `expiry` and `roll_rule` columns.

#### Scenario: migration round-trip
- **WHEN** `alembic upgrade head`, `downgrade -1`, `upgrade head` run
- **THEN** all succeed and there is exactly one head
