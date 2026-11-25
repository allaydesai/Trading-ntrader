## ADDED Requirements

### Requirement: The research MCP takes a crypto run's fee tier and spread as the CLI does
The research MCP's `submit_backtest` and `validate_config` SHALL accept optional `fee_tier`
(a JSON integer) and `spread_bps` (a JSON number) with the same validation, request fields
and config hash as `backtest run --fee-tier`/`--spread-bps`: a tier outside the shipped
schedule (1-17), a negative or non-finite spread, or either on a symbol with no coin spec
SHALL be refused as `invalid_params` naming the field, and nothing SHALL be queued. Each SHALL
be part of the run's config hash only when set. A study run SHALL refuse both.

#### Scenario: same run as the CLI
- **WHEN** a BTC run is submitted through the MCP with `fee_tier` 4 and `spread_bps` 1, and
  the same run is started with `backtest run --fee-tier 4 --spread-bps 1`
- **THEN** both runs have one config hash and the same `crypto_pairs` terms and charges

#### Scenario: non-crypto request refused
- **WHEN** `submit_backtest` is called for QQQ with `fee_tier` 2
- **THEN** the result is not ok, the error is `invalid_params` saying `fee_tier is for spot
  crypto only`, and no job is queued

#### Scenario: unset keeps the hash
- **WHEN** a request has neither field
- **THEN** it resolves to the config hash it had before the fields existed

#### Scenario: reproduction keeps the overrides
- **WHEN** `reproduce_run` is called for a crypto run that set a fee tier or spread
- **THEN** the reproduction resolves to the stored config hash
