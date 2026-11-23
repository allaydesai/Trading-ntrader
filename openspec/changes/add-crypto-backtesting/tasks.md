# Tasks

Each milestone is one PR against `main`, sized like the FX PRs. Every task starts with the
failing test that proves it, and names its tier. Integration tests run `--forked`. Test runs
stay serial or `-n 2` (4 GB box; no pytest while the dashboard on :3000 runs). Nothing here
starts until Allay has answered the decisions in `design.md` section 13; values marked
UNVERIFIED must be confirmed (or accepted as defaults) before M3, which first charges them,
merges.

## M1. Coin specs, `CurrencyPair` model, engine-acceptance spike

Acceptance: `build_instrument("CRYPTO", "BTC/USD.PAXOS", "BTC", bars)` returns a valid
`CurrencyPair` with size precision 8. Equity, futures and FX dispatch is byte-identical. A forked
engine test settles the account model (decision C8) and proves fractional fills and USD P&L.

- [ ] 1.1 Unit (failing first): `tests/unit/services/crypto/test_coin_specs.py`: the shipped file
      loads BTC, ETH, SOL; per-coin base/quote/precision/increment and venue blocks; fee profiles
      PAXOS and KRAKEN; unknown key, non-USD quote, key != base, increment decimals over
      precision, venue block mismatch, unknown fee profile, unknown schema version all raise
      `CryptoSpecError` naming the dotted field.
- [ ] 1.2 `src/services/crypto/coin_specs.yaml` (`source`/`as_of` per value, UNVERIFIED flagged)
      and `coin_specs.py`.
- [ ] 1.3 Unit (failing first): `is_crypto` in `tests/unit/core/test_position_sizing.py`: true
      only for `info.asset_class == "CRYPTO"`; `is_fx_pair`/`is_fx` stay false for it.
- [ ] 1.4 Implement `is_crypto` (core, duck-typed).
- [ ] 1.5 Component (failing first): `tests/component/services/crypto/test_instrument_builder.py`:
      id `BTC/USD.PAXOS` (parses with the IB adapter's `RE_CRYPTO`), `raw_symbol BTC.USD`,
      currencies, price precision follows bars, size precision 8 / increment 1e-8, margins 1,
      fees 0, `info` terms; the KRAKEN profile builds `BTC/USD.KRAKEN`.
- [ ] 1.6 `build_crypto_pair()`.
- [ ] 1.7 Component (failing first): `test_build_instrument.py`: `CRYPTO` gives a crypto
      `CurrencyPair`; STOCK/ETF/None/FUTURES/FX unchanged; `load_from_catalog` with a crypto row.
- [ ] 1.8 Route `CRYPTO` in `build_instrument`.
- [ ] 1.9 Integration, forked (failing first):
      `tests/integration/core/test_crypto_engine_acceptance.py`: a USD-base venue accepts the
      pair and 4-decimal `LAST` bars; a 0.128 BTC market order fills at 0.128; account models A
      (`MARGIN`, leverage 1, margin 1, prototype gate) and B (`CASH`) run the bracket scenario of
      design section 1; the result is pinned; fill size against a 1-minute bar with volume 2 and 0
      is pinned. **Gate**: record which account model passes; if A fails, re-plan M3 before M2
      merges.
- [ ] 1.10 Docs: `docs/agent/crypto-backtesting.md` (skeleton: ids, specs, UNVERIFIED list,
      spike result), index in `architecture.md`.

## M2. FirstRate crypto importer and the `firstrate-crypto` catalog

Acceptance: `import --format firstrate --asset-class crypto --catalog firstrate-crypto
/srv/market-data/raw/crypto` imports 3 coins x 5 timeframes. Catalog row counts equal source line
counts. Prices read back at 4 decimals, volumes at 8. Daily bars sit at 00:00 UTC. A re-run
skips. Peak RSS on BTC 1-minute stays under 1.5 GB.

- [ ] 2.1 Component (failing first):
      `tests/component/services/firstrate/test_firstrate_crypto_parser.py`: registration for
      `CRYPTO`; intraday and daily rows; **UTC** (a US DST Sunday has exactly 24 hourly bars, no
      shift); daily row `D` at `D 00:00 UTC`; 5-decimal prices rounded half-even to 4 and counted;
      fractional and zero volume; gaps and the integer-volume regime counted as warnings;
      chunking, duplicates, cross-chunk order, invalid rows, empty file.
- [ ] 2.2 Implement `FirstRateCryptoParser` and register it.
- [ ] 2.3 Component (failing first):
      `tests/component/services/firstrate/test_crypto_import_service.py` against a tmp catalog:
      spec-filled rows (`ticker BTC`, `nautilus_id BTC/USD.PAXOS`, `asset_class CRYPTO`,
      `currency USD`); bar directory `BTCUSD.PAXOS-1-HOUR-LAST-EXTERNAL`; read-back precision;
      skip/overwrite; incomplete-marker reimport; unknown coin fails that file only.
- [ ] 2.4 Implement `CryptoImportService` (share helpers with the FX/futures services; no growth
      of `ImportService`/`_run_import` baselines).
- [ ] 2.5 Unit/component (failing first): CLI `--asset-class crypto` defaults to five timeframes
      and dispatches to `run_crypto_import`; dry-run and the source probe accept the crypto rows;
      no DB gives exit 2.
- [ ] 2.6 Implement `src/cli/commands/import_crypto.py` and the dispatch.
- [ ] 2.7 Integration, forked: trimmed real BTC 1-hour (a DST week, a weekend, a gap) and SOL daily
      import; full read-back equals source; the engine accepts the bars.
- [ ] 2.8 Manual: import all three coins into `/srv/market-data/catalogs/firstrate-crypto` (new
      directory; existing catalogs untouched); counts vs `wc -l`; RSS on BTC 1-minute; update the
      project README and `data-pipeline.md`. The raw `README.md` ("crypto: yes") is left for
      Allay (the raw tree is read-only to agents).

## M3. Account, costs and the spot gate

Acceptance: a BTC round trip of 0.128 persists fractional `quantity`, USD `profit_loss` and
commission equal to a hand calculation for both fee profiles. Short and levered orders are
denied with the pinned reasons. Equity, futures and FX runs produce byte-identical results and
snapshots.

- [ ] 3.1 Component (failing first): `tests/component/core/test_crypto_costs.py`: PAXOS 18 bp
      with USD 1.75 minimum and 1% cap (USD 9,984 gives 17.97; USD 500 gives 1.75; USD 100 gives
      1.00); minimum applied once per order across partial fills; KRAKEN maker vs taker by
      liquidity side; half-spread component; cost-stress scaling; totals per instrument.
- [ ] 3.2 Implement `src/core/crypto_costs.py` (`CryptoCommissionModel`, reusing the FX per-order
      ledger) and route crypto venues in `venue_cost_models`.
- [ ] 3.3 Component (failing first): `tests/component/core/test_margin_gate_crypto.py`:
      `CryptoSpotRequirement` = qty x price; over-allocation denied
      `MARGIN_EXCEEDS_FREE_BALANCE`; a SELL with no position, or a SELL larger than the long, denied
      `CRYPTO_SPOT_NO_SHORT`; bracket exits allowed; all futures/FX gate tests unchanged.
- [ ] 3.4 Implement the requirement and the short rule in `margin_gate.py` (no growth past the
      size caps; split first if needed).
- [ ] 3.5 Integration, forked (failing first): `tests/integration/core/test_crypto_venue_engine.py`:
      crypto venues get the M1-chosen account; the equity venue `add_venue` call is unchanged; a
      mixed crypto/FX venue is refused; balances exact.
- [ ] 3.6 Wire into `backtest_venue.add_backtest_venue`; no orchestrator growth.
- [ ] 3.7 Unit (failing first): persistence and `mark_to_market` with fractional quantities;
      `_check_against_accounts` holds; `StrategyConfigSnapshot` declares `crypto_pairs`,
      `account_currency` and `periods_per_year`, omitted when unset.
- [ ] 3.8 Unit (failing first): `risk_metrics` annualises on 365 for a crypto run and 252
      otherwise (decision C20); implement.
- [ ] 3.9 Manual: Allay confirms (or accepts as defaults) the PAXOS tier, the Kraken tier and the
      spread defaults; record the answer and date in `coin_specs.yaml` and the design.

## M4. Fractional sizing

Acceptance: a USD 10,000 budget on BTC at 78,000 sizes 0.12820512 BTC (floored to 1e-8), not 1.
Below-minimum orders are dropped and counted. Equity, futures and FX sizing unchanged.

- [ ] 4.1 Unit (failing first): `crypto_units_for_notional`/`crypto_units_for_risk`: flooring to
      the increment, no minimum of 1, zero on a sub-minimum order, Decimal in and out.
- [ ] 4.2 Implement in `src/core/position_sizing.py`.
- [ ] 4.3 Component (failing first): `sma_crossover` and `buy_and_hold` on a crypto pair size
      fractionally and submit `instrument.make_qty`; equity/futures/FX unchanged.
- [ ] 4.4 Route the core strategies.
- [ ] 4.5 Unit/component (failing first): `backtest run --units 0.25` accepted for crypto, rejected
      (exit 2) when not a multiple of the increment or for a non-crypto symbol; FX int rule kept;
      MCP `submit_backtest`/`validate_config` follow the same rules.
- [ ] 4.6 Implement the CLI/MCP validation (`sizing_overrides.py`).

## M5. Reporting, dashboard, CLI and MCP

Acceptance: `show`/`report`/`history`, MCP and the dashboard show a crypto block with fractional
quantities, USD notional, underlying return and the costs split. Equity, futures and FX output
byte-identical (goldens captured before the change).

- [ ] 5.1 Unit (failing first): `src/services/crypto/returns.py` figures (design section 10).
- [ ] 5.2 Implement `returns.py` and `display.py`.
- [ ] 5.3 Unit/component (failing first): CLI blocks and `--symbol BTC|BTCUSD|BTC/USD`
      resolution, `--timeframe` to `-LAST`, `--fee-profile`, `--spread-bps`.
- [ ] 5.4 Implement the CLI changes and `price_type_for(CRYPTO)`.
- [ ] 5.5 Component (failing first): MCP `get_run`/`compare_runs`/`export_results` crypto object
      (additive; existing payloads unchanged).
- [ ] 5.6 Implement the MCP changes.
- [ ] 5.7 Capture FX goldens if missing; then component (failing first): dashboard crypto panel,
      badge and trades-table columns; equity/futures/FX goldens unchanged.
- [ ] 5.8 Implement the dashboard files listed in design section 10 and regenerate
      `FROZEN_VIEWS` for exactly those files under decision C12. Browser check via the
      `e2e-ui-testing` skill.

## M6. Strategies (submodule PR + pointer bump)

Acceptance: Riptide and Bollinger Reversal run on BTC/ETH/SOL 1-day and 1-hour with fractional
sizes, instrument-precision prices and no denied orders other than the pinned no-short ones.
Equity and FX results for the existing smoke configs are unchanged.

- [ ] 6.1 Submodule unit tests (failing first): `_sizing` crypto branch; Riptide price rounding at
      instrument precision for crypto; Bollinger bracket precision; Bollinger weekly bucket stays
      UTC ISO week for crypto; long-only on crypto logs once.
- [ ] 6.2 Implement in the submodule; open the submodule PR.
- [ ] 6.3 Configs `configs/riptide_crypto.yaml`, `configs/bollinger_crypto.yaml`.
- [ ] 6.4 Bump the submodule pointer (no `.gitmodules` change); smoke runs BTC/ETH/SOL 1-day
      2021-2025 and BTC 1-hour 2024, results recorded in `docs/agent/crypto-backtesting.md`.

## M7. Kraken path correction

Acceptance: `--data-source kraken` never builds a spot instrument from perpetual prices; it
fetches the spot series or refuses with a clear message; its instrument and fees come from
`coin_specs.yaml`.

- [ ] 7.1 Manual/spike: confirm against the live Kraken API whether the Charts API serves spot
      OHLC (tick type, symbol) and how far back; record the answer (removes the UNVERIFIED in
      design section 11).
- [ ] 7.2 Unit (failing first): `KrakenPairMapper` spot charts symbol (or the refusal when the
      range is older than the REST window); the mapper never returns a `PF_` symbol for spot.
- [ ] 7.3 Implement; move code out of `kraken_client.py`/`_backtest_helpers.py` (no baseline growth).
- [ ] 7.4 Component (failing first): the fetched instrument is spec-built (`BTC/USD.KRAKEN`,
      KRAKEN fee profile), the venue gets `CryptoCommissionModel`, and the fallback for a coin
      without a spec uses the KRAKEN profile.
- [ ] 7.5 Implement; update `KrakenSettings` docs and the README.
- [ ] 7.6 Archive: `/openspec-archive-change add-crypto-backtesting` once M7 merges, so
      `openspec/specs/crypto-*` matches the code.

No task touches the live/paper path (`src/core/live_*`, `src/cli/commands/live*`), so there is
no live-verification task; the live effort (design section 12) will need its own.
