# Proposal: spot FX backtesting (M1-M6)

## Why

NTrader can backtest equities, ETFs and (after PRs #48-#58) continuous futures, but not spot
FX. FirstRate FX bars for EURUSD, GBPUSD and USDJPY (2010-01-03 to 2026-04-30, all five
timeframes) sit under `/srv/market-data/raw/fx/`, and the README marks them "no parser yet".
Forcing them through today's equity path would produce wrong results without any error:

- **No instrument model.** `build_instrument` routes everything except `FUTURES` to
  `build_equity`, so EURUSD would become a USD `Equity` with no base currency.
- **P&L in the wrong currency.** Backtest venues are multi-currency margin accounts (no
  `base_currency`). A USDJPY position realises P&L in JPY, but
  `backtest_persistence` parses `"-15000.00 JPY"` into a currency-less `profit_loss`,
  `results_extractor._fills` raises on non-USD commission, and `mark_to_market` refuses
  non-USD-quoted instruments. So a JPY trade is either refused or recorded as 15,000 "dollars".
- **Costs.** The IBKR per-share model ($0.005/unit, $1 minimum) would charge
  100,000 EUR like 100,000 shares ($500). IB charges about $2.
- **Sizing.** `notional_quantity` sizes as `budget / price`. That is right for EURUSD (USD
  quote) but wrong by about 157x for USDJPY, where the budget is already in the base
  currency.
- **Strategies.** Riptide and Bollinger Reversal round prices to cents (`quantize(0.01)`,
  `Price.from_str(f"{x:.2f}")`), so a 1.08431 EURUSD level becomes 1.08 and Nautilus denies
  the order on precision. Riptide's dollar-volume liquidity filter reads FX tick volume.

This change plans FX support in the same shape as futures: one reviewable PR per
milestone. It keeps the model compatible with later live trading on IB IDEALPRO, which is a
separate effort.

## What changes (by milestone; details in `design.md`, tasks in `tasks.md`)

- **M1: instrument model.** A versioned, hand-curated `src/services/fx/pair_specs.yaml`
  (base, quote, pip size, price precision, IB min tick, size increment, typical spread,
  margin %, commission, IB `CASH`/`IDEALPRO` block) validated by pydantic with
  `FxSpecError`. `build_fx_pair()` returns a Nautilus `CurrencyPair` with id
  `EUR/USD.IDEALPRO` (IB_SIMPLIFIED symbology). `build_instrument` routes `FX` to it. An
  engine-acceptance spike proves that a USD-base account converts USDJPY P&L with the chosen
  rate mechanism.
- **M2: importer.** `FirstRateFxParser` for the FX layout (`YYYYMMDD,HH:MM:SS,O,H,L,C,V`
  intraday, `YYYYMMDD,O,H,L,C,V` daily) and `FxImportService`, both streaming, at a fixed
  per-pair precision. They write the `firstrate-fx` catalog as `MID` bars. Daily bars are
  stamped at their 17:00 New York session open. Weekend and holiday anomalies produce
  warnings.
- **M3: account-currency conversion.** FX venues get a single-currency USD account.
  `FxRateFeeder` keeps Nautilus' exchange-rate table current from the bars. Trade
  `profit_loss`, commissions, mark-to-market equity and every metric come out in USD for
  USD-quoted, USD-base and cross pairs.
- **M4: costs, margin, sizing.** `FxCommissionModel` (bps of the USD trade value with a
  per-order minimum, plus a modelled half-spread per side), margin as a percentage of notional
  enforced by a generalised margin gate, and sizing helpers in base-currency units.
- **M5: reporting, dashboard, CLI.** Pips, USD notional, leverage and lots in
  `show`/`report`/`history`/MCP and the dashboard (needs a FROZEN_VIEWS ruling).
  `backtest run --units N`. Timeframe mapping to `-MID` bars for FX symbols.
- **M6: strategies.** Submodule PR plus bump: Riptide and Bollinger Reversal use instrument
  precision and quote-to-USD-aware sizing. Bollinger's ISO-week bucketing follows the New York
  trade date. FX configs disable the dollar-volume filter. Smoke runs on all three pairs.

## Interfaces crossed

- **CLI**: `import --format firstrate --asset-class fx` imports (it exits 2 today with "No
  parser registered"), defaulting to all five timeframes. `backtest run` maps
  `--timeframe` to a `MID` bar type for FX instruments, accepts `EURUSD` or `EUR/USD` as
  `--symbol`, and gains `--units N` (M5). No exit-code changes.
- **Parser registry**: `get_parser(AssetClass.FX)` returns the new parser.
- **Record shape**: FX `catalog_instruments` rows (`asset_class = FX`, `ticker = EURUSD`,
  `nautilus_id = EUR/USD.IDEALPRO`, `currency` = quote, `tick_size` = IB min tick,
  `multiplier = 1`). FX `backtest_runs.config_snapshot` gains `fx_pairs` (terms used) and
  `account_currency`. `trades.profit_loss` is in USD for FX runs.
- **Bar type naming**: FX bars are `EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL`. On disk the
  directory is `EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL` (Nautilus strips `/`).
- **Order events**: FX orders over margin are denied with the existing
  `MARGIN_EXCEEDS_FREE_BALANCE` prefix. Futures reasons are unchanged.
- **MCP tool contract**: `get_run`/`compare_runs`/`export_results` gain `asset_class: "fx"`
  and an `fx` object for FX runs (additive).
- **Report formats**: CSV/JSON gain FX fields for FX runs only.
- **Strategy config**: `units: int | None` on FX-capable strategy configs (M5/M6).
- **No DB migration** (recommended; see decision D11). `3f56b46c78eb` stays the single head.

## Guard lists / pinned constants

- **`FROZEN_VIEWS`**: M5's dashboard edits need a recorded PO ruling, as futures M4 did.
  The files are listed in `design.md`. M1-M4 and M6 touch no frozen file.
- No `src/core/live_*.py` or `src/cli/commands/live*.py` module is touched in M1-M6, so no
  live guard list applies. The live effort will need them (see design, "Live").
- `STRATEGY_MODULES` (globbed) picks up the submodule changes; no list edit.
- The futures margin gate is generalised in M4. Its reason prefixes and every futures
  scenario in `openspec/changes/add-futures-backtest-costs` must stay green unchanged.
- Size caps: new logic goes into new modules (`src/services/fx/`, `src/core/fx_costs.py`,
  `src/core/fx_rates.py`). `BacktestOrchestrator` (277), `run_backtest` (173) and
  `SMACrossover` (101) must not grow.
- `.gitmodules` is not changed. M6 only bumps the submodule pointer.

## Out of scope

- **Live/paper FX trading via IB** (separate effort; compatibility notes in design).
- **Overnight financing / rollover / IB cash-balance interest**: not modelled (decision D9).
  Every FX report states it.
- Real bid/ask data. The source is a single series; spread is modelled.
- Pairs without data on the VPS. Crosses (EURGBP) are supported by the design and tested
  with fixtures, but there is no cross data until a source is added.
- IB historical FX download (`ibkr_data_provider` is `STK`-only). Noted as a follow-up source.
- Non-USD account currencies. The account currency is a parameter but only USD is tested.
- Intraday margin, margin calls, liquidation (same limits as futures).
- 4-hour or custom-session bar aggregation.
