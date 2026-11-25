# Tasks

## 1. Shared validation
- [x] 1.1 Unit (failing first): `tests/unit/mcp_server/test_crypto_costs.py::TestSharedValidator`:
      unset returns the request itself, the CLI's flag names lead its messages, a bool is not
      a tier.
- [x] 1.2 `src/services/crypto/cost_overrides.py`: `apply_crypto_overrides`,
      `CryptoOverrideError`, `with_crypto_terms` (moved); `_crypto_options.py` delegates.
      Existing CLI tests (`test_backtest_crypto_options.py`, `test_backtest_crypto_symbols.py`)
      pass unchanged.

## 2. MCP request
- [x] 2.1 Unit (failing first): `TestHashStability` (pre-change hashes pinned for an ETF, a
      BTC and a BTC `units` request; each override changes the hash; `1` and `1.0` are one
      run), `TestResolve` (request and summary fields, zero spread is set, CLI-equal request,
      0/18/-0.5/inf/nan refused naming the field, non-crypto refused, wrong types refused).
- [x] 2.2 `BacktestSpec.fee_tier`/`spread_bps`, `resolve` applies them, `summary()` reports them.

## 3. Tools, worker, reproduction
- [x] 3.1 Component (failing first): `tests/component/mcp_server/test_crypto_cost_tools.py`:
      both tools advertise the fields; a crypto request validates and queues with the same
      hash and stores them in the spec; unset leaves `resolved` unchanged; non-crypto and
      tier 18 queue nothing with `invalid_params`; a study run refuses them.
- [x] 3.2 `submit_backtest`/`validate_config` inputs and docstrings; study refusal.
- [x] 3.3 Unit: `TestWorker`: the engine gets the pair at tier 4 / 1 bp, unset passes the
      loaded instrument through, a loaded equity row is refused before the engine.
- [x] 3.4 Unit: `TestReproduction`: every combination of set/unset rebuilds the stored hash.
- [x] 3.5 Implement the worker and `reproduce_run` changes.

## 4. Docs and smoke
- [x] 4.1 `docs/agent/crypto-backtesting.md`, `README.md` (MCP paragraph).
- [x] 4.2 Smoke (2026-11-25): `firstrate-crypto` BTC 1-day 2023-01-01..2024-12-31 SMA(10/20),
      USD 100,000, tier 4, 1 bp, once by `backtest run --fee-tier 4 --spread-bps 1` and once
      by MCP `submit_backtest(fee_tier=4, spread_bps=1, params={"portfolio_value": 100000})`:
      config hash `3ddefd58f5d2...` on both, identical `crypto_pairs` (tier 4, `"1.0"` bp,
      commission 1,296.73 taker, spread 18.52 USD), 18 trades with equal commission and P&L,
      final balance 109,174.83; `reproduce_run` resolves both back to the stored hash.
