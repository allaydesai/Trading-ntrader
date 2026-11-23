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

- [x] 2.1 Component (failing first): `tests/component/services/firstrate/test_firstrate_fx_parser.py`:
      registration; 7-field intraday and 6-field daily rows; compact date plus separate time;
      ET to UTC parity with the futures parser (DST weeks, Sunday 17:00 open); the daily row
      `D` is stamped `D-1 17:00 ET`; padding (`1.43` to `1.43000`); USDJPY float noise
      (`108.33599` to `108.336`) rounded and counted; zero volume valid; Saturday and off-hour
      Sunday bars kept and counted as `session_anomalies`; chunking, duplicates, cross-chunk
      order, invalid rows, empty file.
- [x] 2.2 Implement `FirstRateFxParser` and register it.
- [x] 2.3 Component (failing first): `tests/component/services/firstrate/test_fx_import_service.py`
      against a tmp catalog: spec-filled rows (`ticker EURUSD`, `nautilus_id
      EUR/USD.IDEALPRO`, `asset_class FX`, `currency` quote, `tick_size` min tick); bar
      directory `EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL`; read-back precision; skip/overwrite;
      the incomplete-marker reimport; unknown pair fails that file only.
- [x] 2.4 Implement `FxImportService` (share helpers with `FuturesImportService`; no growth of
      `ImportService`/`_run_import` baselines).
- [x] 2.5 Unit/component (failing first): CLI `--asset-class fx` defaults to five timeframes and
      dispatches to `run_fx_import`; dry-run and source probe accept both FX row shapes; no DB
      gives exit 2.
- [x] 2.6 Implement `src/cli/commands/import_fx.py` and the dispatch.
- [x] 2.7 Integration, forked: trimmed real EURUSD 1-hour (a DST week plus a weekend) and USDJPY
      daily (2014 noise rows) import, full read-back equals source, the engine accepts the bars.
- [x] 2.8 Manual: import all three pairs into `/srv/market-data/catalogs/firstrate-fx` (new
      directory; existing catalogs untouched); counts vs `wc -l`; RSS measured on EURUSD 1min;
      update `/srv/market-data/raw/README.md` ("fx: yes"), project README, `data-pipeline.md`.
      **Result (2026-11-22):** 15/15 series imported, every count equal to `wc -l` (table in
      `docs/agent/fx-backtesting.md`), 767 MB on disk, re-run skips all 15, peak RSS 750 MB
      on the 1-minute pass (505 MB for 1day+1hour). Existing catalogs untouched. The raw
      `README.md` is left for Allay: the raw tree is read-only to agents.

## M3. Account-currency conversion (USD results for any pair)

Acceptance: a USDJPY and an EURGBP (fixture) round trip persist `profit_loss` in USD equal to a
hand calculation. Mark-to-market equity and the risk metrics are computed (not the
realised-only fallback). Equity and futures runs produce byte-identical results and snapshots.

- [x] 3.1 Component (failing first): `tests/component/core/test_fx_rates.py`: the feeder writes
      cache quotes and mark xrates from MID bars, publishes nothing on the bus (a strategy
      subscribed to quotes gets none), records misses; conversion-pair resolution: USD quote
      none, USD base itself, cross needs `XXXUSD`/`USDXXX`; missing pair raises naming it.
- [x] 3.2 Implement `src/core/fx_rates.py` (`FxRateFeeder`, `conversion_pairs_for`).
- [x] 3.3 Integration, forked (failing first): `tests/integration/core/test_fx_conversion_engine.py`:
      the orchestrator sets `base_currency=USD` only for FX venues; USDJPY and EURGBP USD balances
      exact; a fill without a rate fails the run; the equity venue `add_venue` call is unchanged;
      with a moving GBPUSD the EURGBP close converts at the fill bar's rate (not the prior bar's).
- [x] 3.4 Wire into `backtest_venue.add_backtest_venue` and the loader (conversion-only
      instruments from the same catalog/timeframe); no orchestrator growth. Two ordering
      rules found by the M1 gate (`test_fx_engine_acceptance.py` pins both):
      (a) register the `FxRateFeeder` **before** any strategy (`add_actor` before
      `add_strategy`), so on each bar the rate is in the cache before a strategy submits
      (covers `on_bar` orders only: a resting stop/limit fills before the feeder sees the
      bar and converts at the previous bar's rate; 3.3 must pin that lag or close it);
      (b) add a cross's rate-source bars (GBPUSD for EURGBP) to the engine **before** the
      traded pair's bars. At equal timestamps bars dispatch in the order added, so the
      reverse order converts at the previous bar's rate (USD 1,220 instead of 1,250).
      **Result (2026-11-22):** the lag is pinned, not closed (`docs/agent/fx-backtesting.md`
      "When a fill converts"): an `on_bar` market order converts at bar N's rate; a resting
      limit/stop at bar N-1's for USDJPY but bar N's for a cross (its GBPUSD bar comes
      first); a gap-aware release at bar N-1's for both. FX venues are charged USD 0.00
      until M4 (the per-share equity model is wrong for FX). Only the MCP worker passes
      `rate_sources`; the CLI and web paths have no FX bar type until M5.
- [x] 3.5 Unit (failing first): `backtest_persistence` parses currency, converts with the exit
      rate, raises without one; `mark_to_market` accepts `CurrencyPair` and builds USD
      equity cut at 17:00 ET for FX; `_check_against_accounts` still holds.
- [x] 3.6 Implement the persistence, extractor and mark-to-market changes; `fx_trade_date` in
      `src/services/fx/sessions.py`.
- [x] 3.7 Unit: `StrategyConfigSnapshot` declares `fx_pairs`/`account_currency`, omitted when
      unset (`tests/unit/models/test_config_snapshot_fx.py`).
- **M3 smoke (2026-11-22):** `sma_crossover` USDJPY 1-HOUR-MID, 2024-01-02 to 2024-02-01,
  `firstrate-fx`, persisted: 10 closed trades, USD balance -6.79 = the sum of the trade rows'
  `profit_loss`, `metrics_basis` `mark_to_market`. Trade 1 by hand: short 691 at 144.657,
  covered at 143.908, 517.559 JPY / 143.908 = USD 3.60 (stored 3.60).

## M4. Costs, margin and sizing

Acceptance: a 100k EURUSD round trip costs `2 x max(0.2bp x notional, $2)` plus
`100k x 0.5 pip` of spread. A 3%-margin account denies an order over 33x free balance. `sma_crossover`
on USDJPY with $100k notional trades 100,000 USD units. Every futures margin-gate scenario
still passes unchanged.

- [x] 4.1 Component (failing first): `tests/component/core/test_fx_costs.py`: bps with the
      per-order minimum across partial fills; USD-quote, USD-base and cross trade values;
      spread drag; cost-stress scaling; `venue_cost_models` picks FX models only for FX venues;
      `fx_snapshot`.
- [x] 4.2 Implement `src/core/fx_costs.py` (`FxCommissionModel`, `fx_snapshot`).
- [x] 4.3 Integration, forked (failing first): `tests/integration/core/test_fx_margin_engine.py`:
      over-margin long/short denied (`MARGIN_EXCEEDS_FREE_BALANCE`), within margin fills,
      closes never denied, quantity-increase modify rejected; then the full futures
      margin-engine suite unchanged.
- [x] 4.4 Generalise `FuturesMarginGate` to `MarginGate` (per-instrument requirement); keep
      the futures module path and reasons as they are.
- [x] 4.5 Unit (failing first): `tests/unit/test_position_sizing.py`: `quote_to_account_rate`,
      `fx_units_for_notional`, `fx_units_for_risk`, `whole_units`; `sma_crossover`/
      `buy_and_hold` FX sizing; equity/futures cases unchanged.
- [x] 4.6 Implement the helpers and the `sma_crossover`/`buy_and_hold` FX branch (SMACrossover
      stays at 101).
- [ ] 4.7 Allay confirms the UNVERIFIED commission, margin, spread, min-tick and size values;
      `pair_specs.yaml` `as_of`/`source` updated. **Open (2026-11-22):** M4 ships the approved
      working defaults, still `status: UNVERIFIED` in `pair_specs.yaml` and flagged
      "UNVERIFIED, Allay to confirm" in `docs/agent/fx-backtesting.md`.
- [x] 4.8 Manual smoke: `sma_crossover` EURUSD and USDJPY 1-hour, 2024 H1, persisted; one trade
      per pair hand-checked (pips, USD P&L, commission, spread).
      **Result (2026-11-22):** run on the M3 smoke window (USDJPY 1-hour, 2024-01-02 to
      2024-02-01) before and after: 100,000 units a trade (M3: ~690), 12 trade rows, USD P&L
      -9.69 to -1,532.09 (= the balance change), commission 46.00 and spread 39.13 (23 orders);
      trade 1 hand-checked to the cent (USD 511.68, table and working in
      `docs/agent/fx-backtesting.md`). 2024 H1 (2024-01-02 to 2024-06-28), persisted: EURUSD
      87 rows, USD -5,517.65, commission 346.07, spread 399.81; USDJPY 77 rows, USD -2,234.33,
      commission 306.00, spread 250.93; each sum of rows = the balance change; one trade per
      pair hand-checked (EURUSD 91,239 units, -43.69 pips, -398.66 - 8.56 = -407.22).
- **M4 notes (2026-11-22):** FX venues need `default_leverage=1` (Nautilus' margin-account
  default 10 made 3% margin 0.3%; pinned). The bar fill model splits large orders into 2-3
  fills, so commission **and** spread are rounded once per order (running totals per
  `client_order_id`), and the odd-lot warning reads the order size. A USDJPY/cross trade's USD
  `profit_loss` now deducts the USD commissions (Nautilus nets commission into `realized_pnl`
  only in the settlement currency). The M3 conversion test runs at `cost_multiplier=0` to keep
  its amounts pure conversion. The FX modify reasons are `FX_QTY_INCREASE_NOT_SUPPORTED` and
  `FX_MODIFY_PENDING`. PR #63 follow-up (2026-11-23): pending modifies are accounted per order
  (`PendingModifies`: each passed modify's unapplied margin increase is held back from free
  balance until the venue applies or rejects it or the order closes), replacing the event
  counter that never cleared on trailing/contingent/reduce-only/emulated/exec-algorithm orders
  and so refused every later FX entry `FX_MODIFY_PENDING`; a resting entry the gate cannot price
  is denied `FX_MARGIN_UNPRICEABLE` (market orders still pass); `nautilus-trader<1.222` pinned
  (1.222+ shrinks USDJPY positions with FX commission). Smoke rerun: USDJPY January 2024 still
  USD -1,532.09 (12 positions, 23 orders).

## M5. Reporting, dashboard and CLI

Acceptance: `show`/`report`/`history`/MCP show pips, lots, USD notional, leverage, costs split
and the "financing not modelled" note for FX runs. `--units N` and `EURUSD`/`EUR/USD` symbols
work. `--timeframe 1-HOUR` loads `MID` bars for FX. The dashboard FX panel renders (after the
D12 ruling). Equity and futures output is byte-identical to the pre-M5 goldens.

- [x] 5.1 Unit (failing first): `tests/unit/services/fx/test_returns.py` (hand-computed EURUSD
      long win plus USDJPY short loss: pips, underlying %, USD notional, margin, peak leverage,
      costs split); `test_display.py`.
- [x] 5.2 Implement `src/services/fx/returns.py`, `display.py`.
- [x] 5.3 Component (failing first): `tests/component/cli/test_backtest_fx_options.py`: symbol
      normalisation, `MID` price type from the catalog row, `--units` validation (exit 2
      cases as `--contracts`), `--spread-pips`.
- [x] 5.4 Wire `price_type_for`, symbol normalisation, `--units`, `--spread-pips` through
      `backtest run` (no growth of `run_backtest`, baseline 173).
- [x] 5.5 Unit (failing first) and implement: the FX blocks in `show.py`, `report.py`,
      `history.py`, MCP `runs.py`/`export.py`.
- [x] 5.6 Dashboard after the D12 ruling: goldens first (pre-M5 tree, equity and futures runs),
      component tests red first (`tests/component/api/test_fx_dashboard.py`), then the
      panel, trades table, export, list badge and compare; regenerate `FROZEN_VIEWS` for
      exactly the approved files and record the diff in `design.md`.
- [ ] 5.7 Manual: FX run pages and trades tables on 127.0.0.1:3000 return 200 with the FX
      numbers.
- [x] 5.8 Import summary (found in M2/M4 smokes): "Flagged (OHLC)" counted the FX counters
      (rounding, session anomalies) as OHLC flags; `ImportResult.ohlc_flags` splits them into
      an "Other warnings" row (shown only when non-zero). The FX dry-run labels `-MID`.
- **M5 notes (2026-11-23):** a cross has no USD notional/margin/leverage figure (its
  quote/USD rate at entry is not stored; D11 adds no column). `--spread-pips` is a CLI flag
  and part of the config hash; the `FX_SPREAD_PIPS_<PAIR>` env setting is not implemented
  (outside the hash, it would make runs unreproducible). `run_backtest` stays at 173 (a
  no-op `UsageError` re-raise dropped); `BacktestRequest` shrinks to 179. The CLI and the
  MCP/web request paths share `src/services/fx/symbols.py`. Smokes in
  `docs/agent/fx-backtesting.md` ("Smoke (task 5.7)").

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
