# crypto-instruments Specification

## Purpose
Hand-curated, versioned crypto coin specs (validated on load) and the fractional Kraken spot
`CurrencyPair` instruments built from them. Created by archiving change `add-crypto-backtesting`
(`openspec/changes/archive/2026-11-25-add-crypto-backtesting/`).

## Requirements

### Requirement: Crypto coin specs are hand-curated and versioned
The system SHALL load spot crypto specs from a versioned YAML file holding, per coin, base and
quote currency, price and size precision, size increment, typical spread in basis points, the
Kraken symbols and minimums, plus the dated Kraken Pro spot fee schedule (every tier's maker
and taker rate) and the default tier.

#### Scenario: shipped file
- **WHEN** the shipped spec file is loaded
- **THEN** BTC, ETH and SOL are returned with quote USD, size precision 8 and size increment
  0.00000001, and the fee schedule has 17 tiers with tier 1 at 0.40% maker and 0.80% taker

#### Scenario: Kraken pair terms match Kraken's AssetPairs
- **WHEN** the shipped spec file is loaded
- **THEN** each coin's Kraken REST name, altname, minimum order (base and USD) and tick size
  equal Kraken's public AssetPairs values (SOL's REST name is `SOLUSD`, BTC's minimum order is
  0.00006 BTC), each with a VERIFIED source naming that endpoint

### Requirement: Invalid coin specs are rejected
Loading SHALL reject an invalid spec file with `CryptoSpecError` naming the file and the dotted
field path, including any quote currency other than USD and a fee schedule whose tiers are not
numbered 1..N or whose maker rate exceeds its taker rate.

#### Scenario: non-USD quote
- **WHEN** a coin declares `quote: USDT`
- **THEN** loading raises `CryptoSpecError` naming the field

### Requirement: Spot crypto instruments are fractional Kraken currency pairs
The system SHALL build a Nautilus `CurrencyPair` for a crypto catalog row with id
`{BASE}/USD.KRAKEN`, the spec's currencies and size precision, price precision taken from the
bars (or the spec), margin fractions of 1, zero instrument fees, and
`info.asset_class = "CRYPTO"`. Spot FX helpers SHALL NOT treat it as FX.

#### Scenario: build BTC
- **WHEN** `build_instrument("CRYPTO", "BTC/USD.KRAKEN", "BTC", bars)` is called with
  4-decimal bars
- **THEN** it returns a `CurrencyPair` with base BTC, quote USD, price precision 4, size
  precision 8, and `is_fx` false

#### Scenario: other asset classes unchanged
- **WHEN** a STOCK, ETF, FUTURES, FX or unset row is loaded
- **THEN** the instrument equals what `build_instrument` returned before this change
