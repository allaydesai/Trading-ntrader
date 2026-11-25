# Proposal: crypto fee tier and spread on the research MCP

## Why

`backtest run` takes `--fee-tier N` (Kraken Pro spot tier 1-17, crypto M3) and
`--spread-bps X` (modelled full spread, M5) for spot crypto runs. The research MCP's
`submit_backtest` and `validate_config` do not: `BacktestSpec` forbids extra fields, so an
MCP crypto run always charges tier 2 at the coin spec's spread, and a cost-sensitivity check
from chat has to drop to the CLI. The worker also never rebuilt the loaded pair at a run's
tier or spread, so a request carrying them could not have charged them.

## What changes

- `src/services/crypto/cost_overrides.py` (new): `apply_crypto_overrides` (the CLI's tier,
  spread and coin checks, with the caller's field names in every message) and
  `with_crypto_terms` (moved from `src/cli/commands/_crypto_options.py`, which now delegates
  and re-exports it). The MCP may not import `src.cli` (`test_import_safety`), so the shared
  code lives in services.
- `BacktestSpec.fee_tier` (strict int) and `spread_bps` (strict int or float), applied in
  `resolve` by `apply_crypto_overrides`; a refusal is MCP `invalid_params` with the CLI's
  message under the MCP field name. `ResolvedRequest.summary()` gains `fee_tier`/`spread_bps`
  only when set.
- `submit_backtest` and `validate_config` take both. A study run (`study=...`) refuses them for
  now: a frozen candidate records no tier or spread, so its out-of-sample run and candidate
  hash could not carry them.
- The worker rebuilds the loaded pair with `with_crypto_terms`, as `execute_backtest` does; a
  loaded non-crypto instrument is `invalid_params` before the engine starts.
- `reproduce_run` recovers a crypto run's tier and spread from `config_snapshot.fee_tier` and
  `crypto_pairs[...].typical_spread_bps`, keeping the combination whose hash matches the stored
  config hash (the snapshot records the terms charged, not whether they were set).
- A spread is converted through `float`, as click hands the CLI its value, so `1` and `1.0`
  are saved alike (`"1.0"`) and CLI snapshots stay byte-identical.

## Impact

- MCP tool contract: two optional inputs on `submit_backtest`/`validate_config`, two optional
  keys in `resolved`. Requests without them resolve to the same request and config hash
  (pinned against hashes computed on `main` before the change).
- CLI: behaviour unchanged; the non-finite `--spread-bps` message now reads `must be a finite
  number of bps >= 0` (the shared check also refuses a negative spread, which click's
  `FloatRange(min=0)` already refuses first).
- Not in scope: study-level cost overrides (needs the candidate record to carry them), FX
  `spread_pips` on the MCP, and the MCP's `portfolio_value` default (the CLI sets it to the
  starting balance; the MCP keeps the strategy YAML's), which predates this change.
