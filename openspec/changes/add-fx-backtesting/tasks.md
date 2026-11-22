# Tasks

Each milestone is one PR against `main`, sized like the futures PRs (#48, #50, #52, #53,
#55/#56). Every task starts with the failing test that proves it, and names its tier.
Integration tests run `--forked`. Test runs stay serial or `-n 2` (4 GB box). Values marked
UNVERIFIED in `design.md` must be confirmed by Allay before the milestone that first
*charges* them (M4) merges.

## M1. Pair specs, `CurrencyPair` model, engine-acceptance spike

Acceptance: `build_instrument("FX", "EUR/USD.IDEALPRO", "EURUSD", bars)` returns a valid
`CurrencyPair`. Equity and futures dispatch is byte-identical. A forked engine test proves the
USD-base account plus the rate mechanism convert USDJPY P&L exactly. No migration.

- [x] 1.1 Unit (failing first): `tests/unit/services/fx/test_pair_specs.py`: the shipped file
      loads (EURUSD, GBPUSD, USDJPY, EURGBP); base/quote/pip/precision/IB block per pair;
      unknown key, pip decimals over precision, key != base+quote, IB mismatch, maintenance >
      initial, unknown schema version, unknown pair all raise `FxSpecError` naming the field.
- [x] 1.2 `src/services/fx/pair_specs.yaml` (with `source`/`as_of` per value, UNVERIFIED
      flagged) and `pair_specs.py`.
- [x] 1.3 Component (failing first): `tests/component/services/fx/test_instrument_builder.py`:
      id `EUR/USD.IDEALPRO`, `raw_symbol EUR.USD`, currencies, precision follows bars (5 / 3),
      increments, margin fractions, `info` terms; the id parses with the IB adapter's `RE_CASH`.
- [x] 1.4 `build_fx_pair()`.
- [x] 1.5 Component (failing first): `test_build_instrument.py`: `FX` gives `CurrencyPair`;
      STOCK/ETF/None/FUTURES unchanged; `load_from_catalog` with an FX row.
- [x] 1.6 Route `FX` in `build_instrument`.
- [x] 1.7 Integration, forked (failing first): `tests/integration/core/test_fx_engine_acceptance.py`:
      `MARGIN` venue `base_currency=USD` accepts the pairs and `MID` bars; a USDJPY round
      trip without a rate leaves the USD balance unchanged and logs "Cannot calculate
      account state" (pinned); with a prototype `FxRateFeeder` the balance changes by
      `pnl_jpy / rate`; an EURGBP trade with GBPUSD as a conversion-only instrument does
      too. **Gate**: if this fails, apply D4's fallback and re-plan M3 before M2 merges.
      **Result (2026-11-22): passed.** USDJPY 100k 150.000 to 151.000 moves the USD balance
      by exactly USD 662.25; EURGBP via GBPUSD by USD 1,250.00; no rate leaves it frozen.
      D4 stands, no fallback. M3 constraints: add the feeder before the strategies, and
      a cross's conversion bars before the traded bars (else a one-bar rate lag, pinned).
- [x] 1.8 Docs: `docs/agent/fx-backtesting.md` (skeleton: ids, specs, UNVERIFIED list),
      index in `architecture.md`.

## M2. FirstRate FX importer and the `firstrate-fx` catalog

Acceptance: `import --format firstrate --asset-class fx --catalog firstrate-fx
/srv/market-data/raw/fx` imports all 3 pairs x 5 timeframes. Catalog row counts equal source
line counts. Every price reads back at 5/3 decimals. Daily bars sit at 17:00 ET of the
previous day. A re-run skips. Peak RSS on a 1-minute file stays under 1.5 GB.

- [ ] 2.1 Component (failing first): `tests/component/services/firstrate/test_firstrate_fx_parser.py`:
      registration; 7-field intraday and 6-field daily rows; compact date plus separate time;
      ET to UTC parity with the futures parser (DST weeks, Sunday 17:00 open); the daily row
      `D` is stamped `D-1 17:00 ET`; padding (`1.43` to `1.43000`); USDJPY float noise
      (`108.33599` to `108.336`) rounded and counted; zero volume valid; Saturday and off-hour
      Sunday bars kept and counted as `session_anomalies`; chunking, duplicates, cross-chunk
      order, invalid rows, empty file.
- [ ] 2.2 Implement `FirstRateFxParser` and register it.
- [ ] 2.3 Component (failing first): `tests/component/services/firstrate/test_fx_import_service.py`
      against a tmp catalog: spec-filled rows (`ticker EURUSD`, `nautilus_id
      EUR/USD.IDEALPRO`, `asset_class FX`, `currency` quote, `tick_size` min tick); bar
      directory `EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL`; read-back precision; skip/overwrite;
      the incomplete-marker reimport; unknown pair fails that file only.
- [ ] 2.4 Implement `FxImportService` (share helpers with `FuturesImportService`; no growth of
      `ImportService`/`_run_import` baselines).
- [ ] 2.5 Unit/component (failing first): CLI `--asset-class fx` defaults to five timeframes and
      dispatches to `run_fx_import`; dry-run and source probe accept both FX row shapes; no DB
      gives exit 2.
- [ ] 2.6 Implement `src/cli/commands/import_fx.py` and the dispatch.
- [ ] 2.7 Integration, forked: trimmed real EURUSD 1-hour (a DST week plus a weekend) and USDJPY
      daily (2014 noise rows) import, full read-back equals source, the engine accepts the bars.
- [ ] 2.8 Manual: import all three pairs into `/srv/market-data/catalogs/firstrate-fx` (new
      directory; existing catalogs untouched); counts vs `wc -l`; RSS measured on EURUSD 1min;
      update `/srv/market-data/raw/README.md` ("fx: yes"), project README, `data-pipeline.md`.

## M3. Account-currency conversion (USD results for any pair)

Acceptance: a USDJPY and an EURGBP (fixture) round trip persist `profit_loss` in USD equal to a
hand calculation. Mark-to-market equity and the risk metrics are computed (not the
realised-only fallback). Equity and futures runs produce byte-identical results and snapshots.

- [ ] 3.1 Component (failing first): `tests/component/core/test_fx_rates.py`: the feeder writes
      cache quotes and mark xrates from MID bars, publishes nothing on the bus (a strategy
      subscribed to quotes gets none), records misses; conversion-pair resolution: USD quote
      none, USD base itself, cross needs `XXXUSD`/`USDXXX`; missing pair raises naming it.
- [ ] 3.2 Implement `src/core/fx_rates.py` (`FxRateFeeder`, `conversion_pairs_for`).
- [ ] 3.3 Integration, forked (failing first): `tests/integration/core/test_fx_conversion_engine.py`:
      the orchestrator sets `base_currency=USD` only for FX venues; USDJPY and EURGBP USD balances
      exact; a fill without a rate fails the run; the equity venue `add_venue` call is unchanged.
- [ ] 3.4 Wire into `backtest_venue.add_backtest_venue` and the loader (conversion-only
      instruments from the same catalog/timeframe); no orchestrator growth.
- [ ] 3.5 Unit (failing first): `backtest_persistence` parses currency, converts with the exit
      rate, raises without one; `mark_to_market` accepts `CurrencyPair` and builds USD
      equity cut at 17:00 ET for FX; `_check_against_accounts` still holds.
- [ ] 3.6 Implement the persistence, extractor and mark-to-market changes; `fx_trade_date` in
      `src/services/fx/sessions.py`.
- [ ] 3.7 Unit: `StrategyConfigSnapshot` declares `fx_pairs`/`account_currency`, omitted when
      unset (`tests/unit/models/test_config_snapshot_fx.py`).

## M4. Costs, margin and sizing

Acceptance: a 100k EURUSD round trip costs `2 x max(0.2bp x notional, $2)` plus
`100k x 0.5 pip` of spread. A 3%-margin account denies an order over 33x free balance. `sma_crossover`
on USDJPY with $100k notional trades 100,000 USD units. Every futures margin-gate scenario
still passes unchanged.

- [ ] 4.1 Component (failing first): `tests/component/core/test_fx_costs.py`: bps with the
      per-order minimum across partial fills; USD-quote, USD-base and cross trade values;
      spread drag; cost-stress scaling; `venue_cost_models` picks FX models only for FX venues;
      `fx_snapshot`.
- [ ] 4.2 Implement `src/core/fx_costs.py` (`FxCommissionModel`, `fx_snapshot`).
- [ ] 4.3 Integration, forked (failing first): `tests/integration/core/test_fx_margin_engine.py`:
      over-margin long/short denied (`MARGIN_EXCEEDS_FREE_BALANCE`), within margin fills,
      closes never denied, quantity-increase modify rejected; then the full futures
      margin-engine suite unchanged.
- [ ] 4.4 Generalise `FuturesMarginGate` to `MarginGate` (per-instrument requirement); keep
      the futures module path and reasons as they are.
- [ ] 4.5 Unit (failing first): `tests/unit/test_position_sizing.py`: `quote_to_account_rate`,
      `fx_units_for_notional`, `fx_units_for_risk`, `whole_units`; `sma_crossover`/
      `buy_and_hold` FX sizing; equity/futures cases unchanged.
- [ ] 4.6 Implement the helpers and the `sma_crossover`/`buy_and_hold` FX branch (SMACrossover
      stays at 101).
- [ ] 4.7 Allay confirms the UNVERIFIED commission, margin, spread, min-tick and size values;
      `pair_specs.yaml` `as_of`/`source` updated.
- [ ] 4.8 Manual smoke: `sma_crossover` EURUSD and USDJPY 1-hour, 2024 H1, persisted; one trade
      per pair hand-checked (pips, USD P&L, commission, spread).

## M5. Reporting, dashboard and CLI

Acceptance: `show`/`report`/`history`/MCP show pips, lots, USD notional, leverage, costs split
and the "financing not modelled" note for FX runs. `--units N` and `EURUSD`/`EUR/USD` symbols
work. `--timeframe 1-HOUR` loads `MID` bars for FX. The dashboard FX panel renders (after the
D12 ruling). Equity and futures output is byte-identical to the pre-M5 goldens.

- [ ] 5.1 Unit (failing first): `tests/unit/services/fx/test_returns.py` (hand-computed EURUSD
      long win plus USDJPY short loss: pips, underlying %, USD notional, margin, peak leverage,
      costs split); `test_display.py`.
- [ ] 5.2 Implement `src/services/fx/returns.py`, `display.py`.
- [ ] 5.3 Component (failing first): `tests/component/cli/test_backtest_fx_options.py`: symbol
      normalisation, `MID` price type from the catalog row, `--units` validation (exit 2
      cases as `--contracts`), `--spread-pips`.
- [ ] 5.4 Wire `price_type_for`, symbol normalisation, `--units`, `--spread-pips` through
      `backtest run` (no growth of `run_backtest`, baseline 173).
- [ ] 5.5 Unit (failing first) and implement: the FX blocks in `show.py`, `report.py`,
      `history.py`, MCP `runs.py`/`export.py`.
- [ ] 5.6 Dashboard after the D12 ruling: goldens first (pre-M5 tree, equity and futures runs),
      component tests red first (`tests/component/api/test_fx_dashboard.py`), then the
      panel, trades table, export, list badge and compare; regenerate `FROZEN_VIEWS` for
      exactly the approved files and record the diff in `design.md`.
- [ ] 5.7 Manual: FX run pages and trades tables on 127.0.0.1:3000 return 200 with the FX
      numbers.

## M6. Strategies: Riptide and Bollinger Reversal on FX

Acceptance: both strategies run on EURUSD and USDJPY 1-day, 2015-2025, from FX configs. Orders
are accepted at instrument precision, sized correctly in USD terms, and trades occur. Equity
and futures results for both strategies are unchanged (pinned by the submodule tests and
one existing equity smoke).

- [ ] 6.1 Submodule tests (failing first): `make_price` rounding (EURUSD 1.084314 stays 5 dp;
      equity output unchanged); FX `notional_quantity`/`risk_quantity` (USDJPY not 157x off);
      Riptide skips the ADV check for FX; Bollinger weekly bucket by NY trade date (the Sunday
      17:00 bar belongs to the new week; equities unchanged); `units` config.
- [ ] 6.2 Implement in the submodule (`_sizing.py`, `riptide.py`, `bollinger_reversal.py`),
      submodule PR merged first.
- [ ] 6.3 Bump the submodule pointer in NTrader (no `.gitmodules` change); `configs/riptide_fx.yaml`,
      `configs/bollinger_fx.yaml`.
- [ ] 6.4 Integration, forked: Bollinger on trimmed real EURUSD daily places brackets that are
      not denied; Riptide on USDJPY daily sizes at notional.
- [ ] 6.5 Manual smoke and the `docs/agent/fx-backtesting.md` strategy guide (unported
      strategies listed); archive this change (`/openspec-archive-change`).

## Not in these milestones

- Live/paper FX via IB (separate effort; design section 12 lists its starting points and guard
  lists).
- `FxCarryModel` (financing), IB historical FX download, more pairs.
