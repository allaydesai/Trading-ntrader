# Tasks

Each milestone is one PR against `main`, sized like the FX PRs. Every task starts with the
failing test that proves it, and names its tier. Integration tests run `--forked`. Test runs
stay serial or `-n 2` (4 GB box; no pytest while the dashboard on :3000 runs). Nothing here
starts until Allay has answered the decisions in `design.md` section 13; values marked
UNVERIFIED must be confirmed (or accepted as defaults) before M3, which first charges them,
merges.

## M1. Coin specs, `CurrencyPair` model, engine-acceptance spike

Acceptance: `build_instrument("CRYPTO", "BTC/USD.KRAKEN", "BTC", bars)` returns a valid
`CurrencyPair` with size precision 8. Equity, futures and FX dispatch is byte-identical. A forked
engine test settles the account model (decision C8) and proves fractional fills and USD P&L.

- [x] 1.1 Unit (failing first): `tests/unit/services/crypto/test_coin_specs.py`: the shipped file
      loads BTC, ETH, SOL; per-coin base/quote/precision/increment and Kraken block; the 17-tier
      Kraken Pro spot schedule (tier 1 0.40/0.80%, tier 17 0.00/0.05%) and `default_tier`;
      unknown key, non-USD quote, key != base, increment decimals over precision, tiers not
      1..N, maker > taker, default tier outside the schedule, unknown schema version all raise
      `CryptoSpecError` naming the dotted field.
- [x] 1.2 `src/services/crypto/coin_specs.yaml` (`source`/`as_of` per value, UNVERIFIED flagged)
      and `coin_specs.py`.
- [x] 1.3 Unit (failing first): `is_crypto` in `tests/unit/test_position_sizing.py`: true
      only for `info.asset_class == "CRYPTO"`; `is_fx_pair`/`is_fx` stay false for it.
- [x] 1.4 Implement `is_crypto` (core, duck-typed).
- [x] 1.5 Component (failing first): `tests/component/services/crypto/test_instrument_builder.py`:
      id `BTC/USD.KRAKEN` (equal to `KrakenPairMapper.to_nautilus_id("BTC/USD")`),
      `raw_symbol XBTUSD`, currencies, price precision follows bars, size precision 8 /
      increment 1e-8, margins 1, fees 0, `info` terms (Kraken symbols, minimums, fee schedule
      `as_of`).
- [x] 1.6 `build_crypto_pair()`.
- [x] 1.7 Component (failing first): `test_build_instrument.py`: `CRYPTO` gives a crypto
      `CurrencyPair`; STOCK/ETF/None/FUTURES/FX unchanged; `load_from_catalog` with a crypto row.
- [x] 1.8 Route `CRYPTO` in `build_instrument`.
- [x] 1.9 Integration, forked (failing first):
      `tests/integration/core/test_crypto_engine_acceptance.py`: a USD-base venue accepts the
      pair and 4-decimal `LAST` bars; a 0.128 BTC market order fills at 0.128; account models A
      (`MARGIN`, leverage 1, margin 1, prototype gate) and B (`CASH`) run the bracket scenario of
      design section 1; the result is pinned; fill size against a 1-minute bar with volume 2 and 0
      is pinned. **Gate**: record which account model passes; if A fails, re-plan M3 before M2
      merges.
      Result: A passes with the gate (and enforces nothing without it); B rejected. Recorded
      in `design.md` section 1 ("Spike result") and decision C8.
- [x] 1.10 Docs: `docs/agent/crypto-backtesting.md` (skeleton: ids, specs, UNVERIFIED list,
      spike result), index in `architecture.md`.

## M2. FirstRate crypto importer and the `firstrate-crypto` catalog

Acceptance: `import --format firstrate --asset-class crypto --catalog firstrate-crypto
/srv/market-data/raw/crypto` imports 3 coins x 5 timeframes. Catalog row counts equal source line
counts. Prices read back at 4 decimals, volumes at 8. Daily bars sit at 00:00 UTC. A re-run
skips. Peak RSS on BTC 1-minute stays under 1.5 GB.

- [x] 2.1 Component (failing first):
      `tests/component/services/firstrate/test_firstrate_crypto_parser.py`: registration for
      `CRYPTO`; intraday and daily rows; **UTC** (a US DST Sunday has exactly 24 hourly bars, no
      shift); daily row `D` at `D 00:00 UTC`; 5-decimal prices rounded half-even to 4 and counted;
      fractional and zero volume; gaps and the integer-volume regime counted as warnings;
      chunking, duplicates, cross-chunk order, invalid rows, empty file.
- [x] 2.2 Implement `FirstRateCryptoParser` and register it.
- [x] 2.3 Component (failing first):
      `tests/component/services/firstrate/test_crypto_import_service.py` against a tmp catalog:
      spec-filled rows (`ticker BTC`, `nautilus_id BTC/USD.KRAKEN`, `asset_class CRYPTO`,
      `currency USD`); bar directory `BTCUSD.KRAKEN-1-HOUR-LAST-EXTERNAL`; read-back precision;
      skip/overwrite; incomplete-marker reimport; unknown coin fails that file only.
- [x] 2.4 Implement `CryptoImportService` (share helpers with the FX/futures services; no growth
      of `ImportService`/`_run_import` baselines).
- [x] 2.5 Unit/component (failing first): CLI `--asset-class crypto` defaults to five timeframes
      and dispatches to `run_crypto_import`; dry-run and the source probe accept the crypto rows;
      no DB gives exit 2.
- [x] 2.6 Implement `src/cli/commands/import_crypto.py` and the dispatch.
- [x] 2.7 Integration, forked: trimmed real BTC 1-hour (a DST week, a weekend, a gap) and SOL daily
      import; full read-back equals source; the engine accepts the bars.
- [x] 2.8 Manual: import all three coins into `/srv/market-data/catalogs/firstrate-crypto` (new
      directory; existing catalogs untouched); counts vs `wc -l`; RSS on BTC 1-minute; update the
      project README and `data-pipeline.md`. The raw `README.md` ("crypto: yes") is left for
      Allay (the raw tree is read-only to agents).
      Result (2026-11-24): 15/15 series, 17,419,616 bars, every count equal to `wc -l`, 924 MB,
      peak RSS 787 MB over the whole run (BTC 1-minute included), 3 min 12 s; a re-run skips
      all 15. Daily bars sit at 00:00 UTC and equal the UTC hourly aggregate (2026-05-01).

## M3. Account, costs and the spot gate

Acceptance: a BTC round trip of 0.128 persists fractional `quantity`, USD `profit_loss` and
commission equal to a hand calculation at tier 1 (taker and maker) and at a chosen tier. Short and levered orders are
denied with the pinned reasons. Equity, futures and FX runs produce byte-identical results and
snapshots.

- [x] 3.1 Component (failing first): `tests/component/core/test_crypto_costs.py`: USD 9,984 of
      BTC costs 79.87 taker / 39.94 maker at tier 1, 59.90 taker / 29.95 maker at the
      default tier 2 and 34.94 taker at tier 4; maker only for a
      fill whose `liquidity_side` is `MAKER` (a resting limit), taker for market, stop and
      marketable-limit fills; no minimum (USD 10 at tier 1 taker costs 0.08); split fills
      sum to the single-fill charge within a cent; half-spread component; cost-stress
      scaling; totals per instrument and per liquidity side.
      Result: cost stress scales before cent rounding (the FX rule), so 2x at tier 2 is
      119.81 + 2.00, not 119.80.
- [x] 3.2 Implement `src/core/crypto_costs.py` (`CryptoCommissionModel`; per-order cent rounding
      shared with the FX model) and route crypto venues in `venue_cost_models`.
- [x] 3.3 Component (failing first): `tests/component/core/test_margin_gate_crypto.py`:
      `CryptoSpotRequirement` = qty x price; over-allocation denied
      `MARGIN_EXCEEDS_FREE_BALANCE`; a SELL with no position, or a SELL larger than the long, denied
      `CRYPTO_SPOT_NO_SHORT`; bracket exits allowed; all futures/FX gate tests unchanged.
- [x] 3.4 Implement the requirement and the short rule in `margin_gate.py` (no growth past the
      size caps; split first if needed).
- [x] 3.5 Integration, forked (failing first): `tests/integration/core/test_crypto_venue_engine.py`:
      crypto venues get the M1-chosen account; the equity venue `add_venue` call is unchanged; a
      mixed crypto/FX venue is refused; balances exact.
- [x] 3.6 Wire into `backtest_venue.add_backtest_venue`; no orchestrator growth. Remove M1's
      refusal of crypto venues (`CRYPTO_NOT_ENABLED`, `test_crypto_venue_guard.py`).
- [x] 3.7 Unit (failing first): persistence and `mark_to_market` with fractional quantities;
      `_check_against_accounts` holds; `StrategyConfigSnapshot` declares `crypto_pairs`,
      `account_currency` and `periods_per_year`, omitted when unset.
- [x] 3.8 Unit (failing first): `risk_metrics` annualises on 365 for a crypto run and 252
      otherwise (decision C20); implement.
- [ ] 3.9 Manual: the default tier is confirmed (tier 2, Allay's Kraken fee screen,
      2026-11-24, recorded in `coin_specs.yaml` and design C21); Allay confirms whether
      BTC/ETH/SOL are on the 0.02% discounted-pair list, and the spread defaults; record the
      answers and date in `coin_specs.yaml` and the design.
      Open (2026-11-24): the tier is confirmed; discounted-pair membership (C23) and the
      spread defaults (C4) still wait on Allay, so they ship as the conservative defaults
      (`discounted_pair: false`, 2/3/5 bp), UNVERIFIED in the spec file and the snapshot.
- [x] 3.10 Component (failing first, moved from 5.3 at Allay's request):
      `tests/component/cli/test_backtest_crypto_options.py`: `backtest run --fee-tier N`
      reaches `BacktestRequest.crypto_fee_tier` (config hash only when set) and the pair's
      `info["fee_tier"]`; a tier outside 1..17 or a symbol with no coin spec exits 2.
      Implemented in `src/cli/commands/_crypto_options.py`; `run_backtest` and
      `BacktestRequest` grow one line each (baseline entries bumped with the reason).

## M4. Fractional sizing

Acceptance: a USD 10,000 budget on BTC at 78,000 sizes 0.12820512 BTC (floored to 1e-8), not 1.
Below-minimum orders are dropped and counted. Equity, futures and FX sizing unchanged.

- [x] 4.1 Unit (failing first): `crypto_units_for_notional`/`crypto_units_for_risk`: flooring to
      the increment, no minimum of 1, zero on a sub-minimum order, Decimal in and out.
- [x] 4.2 Implement in `src/core/position_sizing.py`.
- [x] 4.3 Component (failing first): `sma_crossover` and `buy_and_hold` on a crypto pair size
      fractionally and submit `instrument.make_qty`; equity/futures/FX unchanged.
- [x] 4.4 Route the core strategies.
- [x] 4.5 Unit/component (failing first): `backtest run --units 0.25` accepted for crypto, rejected
      (exit 2) when not a multiple of the increment or for a non-crypto symbol; FX int rule kept;
      MCP `submit_backtest`/`validate_config` follow the same rules.
- [x] 4.6 Implement the CLI/MCP validation (`sizing_overrides.py`).
      Result (2026-11-24): BTC/ETH 1-day 2023-2024 on `firstrate-crypto` size 0.14-0.40 BTC and
      2.7-6.0 ETH at 10% notional, exactly 0.25 with `--units 0.25`; a USD 0.50 budget drops
      every order (warned once, count logged on stop); smoke table in
      `docs/agent/crypto-backtesting.md` ("Fractional sizing (M4)").

## M5. Reporting, dashboard, CLI and MCP

Acceptance: `show`/`report`/`history`, MCP and the dashboard show a crypto block with fractional
quantities, USD notional, underlying return and the costs split. Equity, futures and FX output
byte-identical (goldens captured before the change).

- [x] 5.1 Unit (failing first): `src/services/crypto/returns.py` figures (design section 10).
- [x] 5.2 Implement `returns.py` and `display.py`.
- [x] 5.3 Unit/component (failing first): CLI blocks and `--symbol BTC|BTCUSD|BTC/USD`
      resolution, `--timeframe` to `-LAST`, `--spread-bps` (`--fee-tier N` landed in M3, 3.10).
- [x] 5.4 Implement the CLI changes and `price_type_for(CRYPTO)`.
- [x] 5.5 Component (failing first): MCP `get_run`/`compare_runs`/`export_results` crypto object
      (additive; existing payloads unchanged).
- [x] 5.6 Implement the MCP changes.
- [x] 5.7 Capture FX goldens if missing; then component (failing first): dashboard crypto panel,
      badge and trades-table columns; equity/futures/FX goldens unchanged.
- [x] 5.8 Implement the dashboard files listed in design section 10 and regenerate
      `FROZEN_VIEWS` for exactly those files under decision C12. Browser check via the
      `e2e-ui-testing` skill.
      Result (2026-11-24): see design section 10 "M5 result". The below-minimum drop count is
      saved as `config_snapshot.crypto_pairs[<id>].orders_below_minimum` (no migration).
      Symbol forms also cover `XBTUSD`, `XXBTZUSD` and `BTC/USD.KRAKEN`, in the MCP request
      and the Run form too. Browser check on 127.0.0.1:3000 (headless Chromium) against real
      runs: BTC tier 3 (18 trades, 0.14-0.40 BTC), a USD 5 BTC run (37 drops recorded), an FX
      run (no crypto panel) and the run list (CRYPTO badge); no console errors.

## M6. Strategies (submodule PR + pointer bump)

Acceptance: Riptide and Bollinger Reversal run on BTC/ETH/SOL 1-day and 1-hour with fractional
sizes, instrument-precision prices and no denied orders other than the pinned no-short ones.
Equity and FX results for the existing smoke configs are unchanged.

- [x] 6.1 Submodule unit tests (failing first): `_sizing` crypto branch; Riptide price rounding at
      instrument precision for crypto; Bollinger bracket precision; Bollinger weekly bucket stays
      UTC ISO week for crypto; long-only on crypto logs once.
- [x] 6.2 Implement in the submodule; open the submodule PR. (strategies #6, merged `32c8322`;
      exit-timing follow-up strategies #7. The gate now lets a reduce-only close take a
      reduce-only stop's claim while its cancel is pending, decision C24.)
- [x] 6.3 Configs `configs/riptide_crypto.yaml`, `configs/bollinger_crypto.yaml` (Bollinger on
      1-day bars, C25).
- [x] 6.4 Bump the submodule pointer (no `.gitmodules` change); smoke runs BTC/ETH/SOL 1-day
      2021-2025 and BTC 1-hour 2024, results recorded in `docs/agent/crypto-backtesting.md`.
      (Pinned to #7's merge `67ebddb`, which includes #6.)

## M7. Kraken data path correction

Acceptance: `--data-source kraken` never builds a spot instrument from perpetual prices; it
fetches the spot series or refuses with a clear message; its instrument and fees come from
`coin_specs.yaml`. Added by Allay for M7 (2026-11-24): Kraken's own price history is imported
(not only evaluated) into a new `kraken-crypto` catalog, smoke-tested on BTC/ETH/SOL with a BTC
comparison against `firstrate-crypto`; `KrakenSettings` default fees move to the Kraken Pro
tier model (tier 2: 0.30% / 0.60%); the M1 UNVERIFIED Kraken pair terms are checked against
Kraken's public endpoints (no API key) and corrected with sources in `coin_specs.yaml`.

- [x] 7.1 Manual/spike: confirm against the live Kraken API whether the Charts API serves spot
      OHLC (tick type, symbol) and how far back; record the answer (removes the UNVERIFIED in
      design section 11). Also check Kraken's downloadable OHLCVT/trade history as a same-venue
      source for BTC/ETH/SOL (coverage, format); record a recommendation.
      Result (2026-11-24, design section 11): no. The Charts API lists only futures symbols
      under `mark`/`spot`/`trade`; its `spot` tick type is the futures index (volume 0, from
      2022-03-22). Spot REST `OHLC` serves the latest 720 candles plus the open one. Kraken's
      OHLCVT archive covers every pair from its first trade to 2026-09-30 (BTC from
      2013-10-06, ETH 2015-08-07, SOL 2021-06-17); recommendation: `kraken-crypto` is the
      primary crypto source, FirstRate a cross-check (median close basis 3.5-6.6 bp since
      2021-07, far wider before).
- [x] 7.2 Unit (failing first): `KrakenPairMapper` spot charts symbol (or the refusal when the
      range is older than the REST window); the mapper never returns a `PF_` symbol for spot.
      `tests/component/services/crypto/test_kraken_spot.py` (component: it builds Nautilus
      bars): REST names Kraken accepts (`SOLUSD`, `XDGUSD`), no `to_kraken_charts`, the
      interval map, the 720-candle window and the refusal before any request.
- [x] 7.3 Implement; move code out of `kraken_client.py`/`_backtest_helpers.py` (no baseline growth).
      `src/services/crypto/kraken_spot.py`; `KrakenHistoricalClient` 145 -> 98 statements
      (baseline entry removed); `_load_kraken_data` no longer builds a fallback pair or
      persists to the default catalog. `run_backtest` 175 -> 176 (the exit-2 refusal, entry
      bumped with the reason).
- [x] 7.4 Component (failing first): the fetched instrument is spec-built (`BTC/USD.KRAKEN`),
      the venue gets `CryptoCommissionModel` at the run's tier, and a coin without a spec gets
      the fallback instrument with the same schedule.
      `tests/component/cli/test_backtest_kraken_source.py`.
- [x] 7.5 Implement; update `KrakenSettings` docs and the README.
- [x] 7.7 Unit (failing first): `tests/unit/services/crypto/test_kraken_ohlcvt_download.py`:
      the split archive reads as one file across part boundaries; only the chosen
      `{ALTNAME}_{minutes}.csv` members are written, with the archive manifest and a
      `SHA256SUMS`; an existing raw file is never overwritten; a missing pair is an error.
      Implemented in `src/services/crypto/kraken_ohlcvt_download.py` (HTTP range reads; ~210 MB
      of the 9.5 GB archive for three coins).
- [x] 7.8 Component (failing first): `tests/component/services/crypto/test_kraken_import.py`
      and `tests/component/cli/commands/test_import_crypto.py`: discovery by spec altname and
      interval (other pairs and intervals ignored), epoch-second UTC stamps, rounding and
      warnings, re-run skip, `import --format kraken` routing, its four timeframes and exit 2
      for 30-minute or another asset class. Implemented in
      `src/services/crypto/kraken_import.py` on the FirstRate crypto service (two hooks: the
      stamp reader and the source-end probe).
- [x] 7.9 Unit: the coin specs' Kraken rest, altname, minimums and tick equal AssetPairs
      (read 2026-11-24) and are VERIFIED with the endpoint as source; spread and
      discounted-pair stay UNVERIFIED. Corrected: BTC `min_order_base` 0.00005 -> 0.00006,
      SOL 0.02 -> 0.05, SOL `rest` SOLZUSD -> SOLUSD.
- [x] 7.10 Unit (failing first): `KrakenSettings` default fees are the schedule's default
      tier (0.0030 / 0.0060, read from `coin_specs.yaml`); connecting needs no API key.
- [x] 7.11 Manual smoke (2026-11-24): download and import BTC/ETH/SOL into
      `/srv/market-data/catalogs/kraken-crypto` (bar counts equal source lines, ranges
      recorded); BTC backtests on `kraken-crypto` vs `firstrate-crypto`; live
      `--data-source kraken` refusal and recent-window runs. Results in
      `docs/agent/crypto-backtesting.md` ("Kraken data (M7)").
- [ ] 7.6 Archive: `/openspec-archive-change add-crypto-backtesting` once M7 merges, so
      `openspec/specs/crypto-*` matches the code.

No task touches the live/paper path (`src/core/live_*`, `src/cli/commands/live*`), so there is
no live-verification task; the live effort (design section 12) will need its own.
