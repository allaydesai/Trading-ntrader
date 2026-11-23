# Proposal: spot crypto backtesting (M1-M7)

## Why

NTrader backtests equities, ETFs, continuous futures and (since the archived
`add-fx-backtesting` change) spot FX end to end. Crypto is the last asset class. FirstRate bars for BTC, ETH and SOL (all five timeframes,
BTC from 2013-04-01, ETH from 2016-03-11, SOL from 2021-03-01, all to 2026-05-02) are in
`/srv/market-data/raw/crypto/`, and the raw README marks them "no parser yet". The only crypto
path today is `--data-source kraken`. Pushing crypto through either path gives wrong results
without any error:

- **No importer and no instrument model.** `import --asset-class crypto` maps to
  `AssetClass.CRYPTO` but no parser is registered (exit 2). A catalog row with
  `asset_class = CRYPTO` would reach `build_instrument`, which sends everything except
  `FUTURES`/`FX` to `build_equity`, so BTC would become a USD `Equity` with whole-share sizing.
- **Sizing is whole units.** `_sizing.notional_quantity`/`risk_quantity` and the core
  strategies return `int` with a minimum of 1, and the strategies submit
  `Quantity.from_int(qty)`. A USD 10,000 BTC budget at USD 78,000 is `max(int(0.128), 1)` =
  **1 BTC (about 7.8x the budget)**. On SOL it is merely rounded down by up to one coin.
- **Costs.** Non-FX, non-futures venues get the IBKR per-share model (USD 0.005 per unit,
  USD 1 minimum), so 0.128 BTC costs USD 1 and 1,000 SOL costs USD 5. Neither matches IBKR's
  crypto schedule (18 bp of trade value, USD 1.75 minimum, published) or Kraken's maker/taker
  percentages. The Kraken fallback instrument's `maker_fee/taker_fee` (0.16%/0.26%) is not
  charged: `venue_cost_models` installs the equity per-share model for any venue without
  futures or FX, and a venue fee model replaces the instrument's fees.
- **Leverage and shorting.** Venues are `MARGIN` with Nautilus' default leverage, and nothing
  stops a spot crypto short or a levered spot position. IBKR's Paxos/Zero Hash crypto and
  Kraken spot (without margin) are long-only, cash-funded.
- **Wrong price series on the Kraken path.** `KrakenPairMapper.to_kraken_charts("BTC/USD")`
  returns `PF_XBTUSD`, Kraken's **perpetual futures** chart symbol, and the bars are wrapped in a
  **spot** `CurrencyPair` priced and charged as spot. Perp prices carry a funding basis.
- **Sessions.** The crypto files are UTC (FirstRate FAQ; confirmed: no DST signature, every
  day has 24 hourly bars, daily bars equal the UTC-midnight aggregate of the hourly bars 1000/1000).
  The equity parser localises every row to `America/New_York`, which would shift crypto bars by
  4-5 hours and split each UTC day.
- **Live.** Nautilus 1.220 has no Kraken adapter, and its IB adapter turns IB `CRYPTO`/`PAXOS`
  contracts into an **inverse** `CryptoPerpetual` (`is_inverse=True`, margin and fees 0). A
  backtest that used that model would settle P&L in BTC, not USD.

This change plans crypto support in the same shape as FX: one reviewable PR per milestone.
It keeps the model compatible with later live trading on IB `PAXOS`, which is a separate
effort.

## What changes (by milestone; details in `design.md`, tasks in `tasks.md`)

- **M1: instrument model and engine spike.** A versioned, hand-curated
  `src/services/crypto/coin_specs.yaml` (base, quote, price precision, size precision and
  increment, minimum order, typical spread, per-venue profiles for `PAXOS` and `KRAKEN` with the
  IB `CRYPTO` contract block and the Kraken REST/charts symbols) validated by pydantic with
  `CryptoSpecError`. `build_crypto_pair()` returns a Nautilus `CurrencyPair` with id
  `BTC/USD.PAXOS` (IB symbology) and fractional size precision. `build_instrument` routes
  `CRYPTO` to it. A forked spike picks the account model for long-only, unlevered spot (a USD
  `MARGIN` account with `margin_init = 1` plus a gate, or a Nautilus `CASH` account) and proves
  fractional quantities, bracket orders and USD P&L work end to end.
- **M2: importer.** `FirstRateCryptoParser` (the equity row layout, but **UTC**, a fixed
  per-coin price precision and 8-decimal coin volume) and `CryptoImportService`, writing the
  `firstrate-crypto` catalog as `-LAST-EXTERNAL` bars. Daily bars are stamped at 00:00 UTC.
  Gaps (sparse early minutes) and the 2026-03-21 volume rounding change are counted as
  warnings.
- **M3: account, costs and the no-short/no-leverage gate.** Crypto venues get the M1-chosen
  account. `CryptoCommissionModel` (a percentage of trade value with an optional per-order
  minimum and a cap, per venue profile, plus a modelled half-spread per side). The generalised
  `MarginGate` gains a `CryptoSpotRequirement` (100% of notional) and denies orders that would
  open or increase a short (`CRYPTO_SPOT_NO_SHORT`). Results, mark-to-market and metrics stay
  in USD with a UTC day.
- **M4: sizing.** Fractional base-unit sizing: `crypto_units_for_notional`,
  `crypto_units_for_risk`, floored to `size_increment`, no minimum of 1, below-minimum orders
  dropped and counted. Core strategies (`sma_crossover`, `buy_and_hold`) and a decimal
  `--units` for crypto.
- **M5: reporting, dashboard, CLI and MCP.** A crypto block in `show`/`report`/`history`
  (coin quantity, USD notional, return on the underlying, fees split, "spot, unlevered,
  long-only"), MCP `asset_class: "crypto"` plus a `crypto` object, the dashboard panel and
  badge (needs a FROZEN_VIEWS ruling), `--symbol BTCUSD|BTC/USD|BTC` resolution and
  `--fee-profile paxos|kraken`.
- **M6: strategies.** Submodule PR plus pointer bump: Riptide and Bollinger Reversal size in
  fractional coin units, round prices at instrument precision, and treat the day as the UTC
  date. Configs `riptide_crypto.yaml`, `bollinger_crypto.yaml`. Smoke runs on BTC/ETH/SOL.
- **M7: Kraken path correction.** `--data-source kraken` fetches the spot series (or refuses
  clearly), builds its instrument through `coin_specs.yaml`, and stops relying on the
  perpetual chart symbol. Kraken-fetched and FirstRate-imported runs report the same
  instrument terms.

## Interfaces crossed

- **CLI**: `import --format firstrate --asset-class crypto` imports (it exits 2 today with
  "No parser registered"), defaulting to all five timeframes. `backtest run` resolves
  `--symbol BTC`, `BTCUSD` or `BTC/USD` to `BTC/USD.PAXOS` for a crypto catalog, and its
  `--units` accepts a decimal for crypto (integers for FX, unchanged). New optional
  `--fee-profile paxos|kraken` and `--spread-bps`. No exit-code changes.
- **Parser registry**: `get_parser(AssetClass.CRYPTO)` returns the new parser.
- **Record shape**: crypto `catalog_instruments` rows (`asset_class = CRYPTO`, `ticker = BTC`,
  `nautilus_id = BTC/USD.PAXOS`, `currency = USD`, `tick_size` = price increment,
  `multiplier = 1`). Crypto `backtest_runs.config_snapshot` gains `crypto_pairs` (terms used)
  and `account_currency`. `trades.quantity` is fractional (the column is already
  `Numeric(20, 8)`).
- **Bar type naming**: `BTC/USD.PAXOS-1-HOUR-LAST-EXTERNAL`; on disk
  `BTCUSD.PAXOS-1-HOUR-LAST-EXTERNAL` (Nautilus strips `/`).
- **Order events**: new deny reasons `CRYPTO_SPOT_NO_SHORT` and, reusing the existing prefix,
  `MARGIN_EXCEEDS_FREE_BALANCE` for unlevered over-allocation. Futures and FX reasons are
  unchanged.
- **MCP tool contract**: `get_run`/`compare_runs`/`export_results` gain `asset_class:
  "crypto"` and a `crypto` object (additive). `submit_backtest`/`validate_config` accept a
  decimal `units` for crypto.
- **Kraken data path** (M7): `KrakenPairMapper.to_kraken_charts` and the fallback instrument
  change behaviour; the `--data-source kraken` flag stays.
- **Strategy config**: `units: Decimal | None` on crypto-capable configs (M4/M6).
- **No DB migration** (recommended, decision C11). `3f56b46c78eb` stays the single head.

## Guard lists / pinned constants

- **`FROZEN_VIEWS`**: M5's dashboard edits need a recorded PO ruling, as futures M4 and FX M5
  did. The files are listed in `design.md` section 10. M1-M4, M6 and M7 touch no frozen file.
- No `src/core/live_*.py` or `src/cli/commands/live*.py` module is touched in M1-M7, so no
  live guard list applies (see design section 12 for what the live effort will need).
- `STRATEGY_MODULES` (globbed) picks up the submodule changes; no list edit.
- `MarginGate` gains a requirement kind; its futures and FX reason strings and every scenario in
  `openspec/specs/fx-backtest` and the open futures changes must stay green unchanged.
- `is_fx` / `is_fx_pair` keep their meaning (FX only). A sibling `is_crypto` keys on
  `info.asset_class == "CRYPTO"`, so crypto `CurrencyPair`s never take the FX rate path.
- Size caps: new logic goes into new modules (`src/services/crypto/`, `src/core/crypto_costs.py`).
  `BacktestOrchestrator`, `run_backtest`, `kraken_client.py` (461 lines) and
  `_backtest_helpers.py` must not grow past their baselines; M7 moves code out of them.
- `.gitmodules` is not changed. M6 only bumps the submodule pointer.

## Out of scope

- **Perpetuals and dated crypto futures** (`CryptoPerpetual`, `CryptoFuture`), funding rates,
  liquidation. Recommended as a separate later change (decision C1). CME crypto futures
  (MBT/MET) already fit the futures path with a contract spec, if wanted.
- **Live/paper crypto trading** via IB PAXOS or Kraken (separate effort; notes in design).
- **Crypto-quoted pairs** (ETH/BTC) and stablecoin quotes (USDT/USDC). The model allows a non-USD
  quote later through the FX rate machinery, but only USD-quoted pairs are built and tested.
- **Margin/lending, staking, custody or account fees** (IB's USD 0.15/month Paxos fee).
- Real bid/ask or order-book data. The spread is modelled.
- Coins without data on the VPS. Adding one is a spec entry plus an import.
- IB historical crypto download (`ibkr_data_provider` is `STK`-only). Follow-up source.
