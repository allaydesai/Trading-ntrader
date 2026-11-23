## ADDED Requirements

### Requirement: Crypto coin specs are hand-curated and versioned
The system SHALL load spot crypto specs from a versioned YAML file holding, per coin, base and
quote currency, price and size precision, size increment, typical spread in basis points and
per-venue blocks (`PAXOS` with the IB `CRYPTO` contract fields, `KRAKEN` with the Kraken
symbols), plus one dated fee profile per venue.

#### Scenario: shipped file
- **WHEN** the shipped spec file is loaded
- **THEN** BTC, ETH and SOL are returned with quote USD, size precision 8 and size increment
  0.00000001, and the PAXOS and KRAKEN fee profiles are present

### Requirement: Invalid coin specs are rejected
Loading SHALL reject an invalid spec file with `CryptoSpecError` naming the file and the dotted
field path, including any quote currency other than USD.

#### Scenario: non-USD quote
- **WHEN** a coin declares `quote: USDT`
- **THEN** loading raises `CryptoSpecError` naming the field

### Requirement: Spot crypto instruments are fractional, IB-compatible currency pairs
The system SHALL build a Nautilus `CurrencyPair` for a crypto catalog row with id
`{BASE}/USD.PAXOS` (or `.KRAKEN` for the Kraken profile), the spec's currencies and size
precision, price precision taken from the bars (or the spec), margin fractions of 1, zero
instrument fees, and `info.asset_class = "CRYPTO"`. Spot FX helpers SHALL NOT treat it as FX.

#### Scenario: build BTC
- **WHEN** `build_instrument("CRYPTO", "BTC/USD.PAXOS", "BTC", bars)` is called with
  4-decimal bars
- **THEN** it returns a `CurrencyPair` with base BTC, quote USD, price precision 4, size
  precision 8, an id the IB adapter's crypto symbology parses as `BTC`/`USD`, and `is_fx`
  false

#### Scenario: other asset classes unchanged
- **WHEN** a STOCK, ETF, FUTURES, FX or unset row is loaded
- **THEN** the instrument equals what `build_instrument` returned before this change
