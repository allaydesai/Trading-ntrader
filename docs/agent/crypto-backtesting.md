# Crypto backtesting: coins, instruments, account model

Spot crypto on Kraken, planned in `openspec/changes/add-crypto-backtesting/` (milestones
M1-M7; decisions C1-C25 in `design.md` section 13). **Status: M1-M6 implemented** (coin
specs, instruments, catalog dispatch, engine spike; the FirstRate importer and the
`firstrate-crypto` catalog; the account, Kraken fees and the spot gate; fractional sizing;
reporting, symbols and the dashboard; the `riptide` and `bollinger_reversal` ports, below).
Crypto backtests run (`backtest run --symbol BTC --catalog firstrate-crypto`), charged at the
Kraken Pro tier and held to spot rules. Not yet: the Kraken data path (M7).

## Ids and instruments

| Coin | Catalog `ticker` | Instrument id | `raw_symbol` | Bars |
|---|---|---|---|---|
| BTC | `BTC` | `BTC/USD.KRAKEN` | `XBTUSD` | `BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL` |
| ETH | `ETH` | `ETH/USD.KRAKEN` | `ETHUSD` | `...-LAST-EXTERNAL` |
| SOL | `SOL` | `SOL/USD.KRAKEN` | `SOLUSD` | `...-LAST-EXTERNAL` |

The id is `KrakenPairMapper.to_nautilus_id("BTC/USD")`, so the FirstRate catalog (M2) and the
Kraken fetch path (M7) name the same instrument (decision C2).

- `src/services/crypto/coin_specs.yaml` + `coin_specs.py`: per-coin specs (USD quote only,
  price precision 4, size precision 8 / increment 1e-8, modelled spread, Kraken block) and the
  17-tier Kraken Pro spot fee schedule (`get_fee_schedule()`, default tier 2). Invalid files
  raise `CryptoSpecError` naming the dotted field.
- `src/services/crypto/instrument_builder.py`: `build_crypto_pair(spec, nautilus_id=...,
  price_precision=...)` gives a Nautilus `CurrencyPair` (Nautilus `asset_class`
  `CRYPTOCURRENCY`), margins 1 (unlevered), instrument fees 0 (the M3 venue fee model
  charges), `info.asset_class = "CRYPTO"` plus the Kraken terms, the schedule's
  `source`/`as_of`/`default_tier`, and the UNVERIFIED field list.
- `build_instrument("CRYPTO", ...)` (`src/services/firstrate/backtest_loader.py`) routes a
  catalog row to it, with price precision taken from the bars.
- `is_crypto(instrument)` (`src/core/position_sizing.py`, duck-typed on
  `info.asset_class == "CRYPTO"`) is the predicate later milestones key on. `is_fx`,
  `is_fx_pair` and `needs_conversion` stay false for crypto.

## Data: the `firstrate-crypto` catalog (M2)

`import --format firstrate --asset-class crypto --catalog firstrate-crypto
/srv/market-data/raw/crypto` imported 3 coins x 5 timeframes on 2026-11-24 (details in
`docs/agent/data-pipeline.md`). Bars are `{COIN}/USD.KRAKEN-{tf}-LAST-EXTERNAL` (directory
`BTCUSD.KRAKEN-...`), UTC bar opens, prices at 4 decimals, volume at 8.

| Coin | First bar (UTC) | Last bar (UTC) | 1min / 5min / 30min / 1h / 1d |
|---|---|---|---|
| BTC | 2013-04-01 | 2026-05-02 23:58 | 6,129,198 / 1,334,755 / 228,603 / 114,579 / 4,780 |
| ETH | 2016-03-11 | 2026-05-02 23:59 | 4,996,580 / 1,029,089 / 176,326 / 88,542 / 3,698 |
| SOL | 2021-03-01 | 2026-05-02 23:58 | 2,635,929 / 539,644 / 90,668 / 45,336 / 1,889 |

Warnings worth knowing before a backtest: every series has whole-coin volume from
2026-03-21 00:00 UTC (FirstRate's rounding; C15); 1-minute rows rounded to 4 decimals: BTC
7,534, ETH 5,397, SOL 0; zero-volume 1-minute bars: BTC 1,176, ETH 100, SOL 5; gaps of more
than 10 bars are mostly in 2013-2016 (BTC 1-minute 8,604, longest 429 minutes; ETH 1-minute
7,537, longest 11,303 minutes; ETH hourly 6, longest 376 hours, early 2016).

## UNVERIFIED values (to confirm before M3 merges)

| Value | Where | Default |
|---|---|---|
| Kraken altname (raw symbol) | `coins.*.kraken.altname` | XBTUSD / ETHUSD / SOLUSD |
| Kraken REST symbol | `coins.*.kraken.rest` | mapper output; SOL gives `SOLZUSD`, Kraken may say `SOLUSD` |
| Order minimum (base) | `coins.*.kraken.min_order_base` | 0.00005 BTC / 0.002 ETH / 0.02 SOL |
| Order minimum (USD) | `coins.*.kraken.min_order_usd` | 0.5 |
| Tick size (live only) | `coins.*.kraken.tick_size` | 0.1 / 0.01 / 0.01 |
| Discounted pair (C23) | `coins.*.kraken.discounted_pair` | false |
| Spread (C4) | `coins.*.typical_spread_bps` | 2 / 3 / 5 bp |
| Fee currency | `fee_schedule.fee_currency` | USD (the page does not say) |

The tier rates (tier 1 0.40% maker / 0.80% taker down to tier 17 0.00% / 0.05%), the 0.02%
discounted-pair rebate and the absence of a minimum fee are VERIFIED against
<https://www.kraken.com/ca/pro/fees> (read 2026-11-24). The default tier (C21) is **tier 2**
(0.30% maker / 0.60% taker), confirmed by Allay from his Kraken fee screen on 2026-11-24.

## Account, costs and the spot gate (M3)

- **Account** (`src/core/backtest_venue.py`): a venue listing crypto pairs is `MARGIN`,
  `HEDGING`, `base_currency=USD`, `default_leverage=1`; the pairs' `margin_init` is 1, so an
  open position locks its full notional. A venue mixing crypto with FX or equities is refused.
- **Fees** (`src/core/crypto_costs.py`, `CryptoCommissionModel`): per fill,
  `rate x qty x price`, the tier's **maker** rate when Nautilus marks the fill `MAKER` (an order
  that rested on the book) and **taker** otherwise (market, stop, marketable limit); a
  discounted pair pays 2 bp less (floored at 0); no minimum. Plus a modelled half-spread,
  `qty x price x typical_spread_bps / 2 / 10,000`. Both are scaled by cost stress, then rounded
  to cents once per order and liquidity side (the FX model's `OrderCharges`), so split fills
  add no rounding. USD 9,984 of BTC: tier 2 (default) 59.90 taker / 29.95 maker, tier 1
  79.87 / 39.94, tier 4 34.94 taker, plus 1.00 spread.
- **Tier** (C21): one per run, the schedule's `default_tier` (2) unless
  `backtest run --fee-tier N` (1-17, else exit 2; refused for a symbol with no coin spec). The
  tier rides on the pair as `info["fee_tier"]` and is part of the run's config hash only when
  set.
- **Gate** (`src/core/margin_gate.py`, `CryptoSpotRequirement`): an entry needs its full USD
  notional free (limit or trigger price, else the last price), else
  `MARGIN_EXCEEDS_FREE_BALANCE: 2 BTC/USD.KRAKEN coin(s) need 156000.00 USD initial margin,
  free=2152.42 USD`. A parentless `SELL`, reduce-only included, must close an open long by
  position id and not oversell it: its size less every working sell on it, a bracket's stop
  and take-profit counted once. Else `CRYPTO_SPOT_NO_SHORT: SELL 0.10000000 BTC/USD.KRAKEN
  would go short; ...`. So a strategy must cancel a resting sell before closing the long at
  market (otherwise the resting sell would fill later as a short). One exception (M6): a
  reduce-only sell pending cancel does not claim the long against a reduce-only close, so an
  exit can cancel its reduce-only hard stop and close beside it in the same bar; should the
  cancel fail, the venue fills the later reduce-only order only up to what is left and cancels
  it once the long is flat. Bracket legs pass at
  submission; crypto sells hold no margin and their reprices are never margined;
  quantity-raising modifies are refused `CRYPTO_QTY_INCREASE_NOT_SUPPORTED`.
- **Metrics** (C20): mark-to-market Sharpe, Sortino and volatility annualise on 365 for a run
  with a crypto pair (252 otherwise). If mark-to-market is unavailable, the realised fallback
  is Nautilus' own 252-day statistic, unchanged.
- **Snapshot**: `account_currency` `USD`, `fee_tier`, `periods_per_year` 365 and
  `crypto_pairs[id]`: tier rates, spread, precisions, discounted flag, fee-schedule source and
  as-of, UNVERIFIED fields and `costs_usd` (`commission`, `commission_maker`,
  `commission_taker`, `spread`).

Pinned in `tests/component/core/test_crypto_costs.py`, `test_margin_gate_crypto.py` and
`tests/integration/core/test_crypto_venue_engine.py` (forked).

**Smoke (2026-11-24, real `firstrate-crypto` bars).** `backtest run --symbol BTC --catalog
firstrate-crypto --timeframe 1-day --start 2023-01-01 --end 2024-12-31 --starting-balance
100000` (`sma_crossover`): 18 trades, all taker at tier 2; commission USD 2,222.95 plus 37.05
spread, equal to the trade rows' 2,260.00; final balance 108,230.08; mark-to-market Sharpe 0.93
on 365 days; the strategy's first cross-down `SELL` (0.46220418 BTC, no position) was denied
`CRYPTO_SPOT_NO_SHORT`. On 1-hour bars (2024-03-01 to 03-07) a resting limit buy of 0.5 BTC
filled as maker (91.40 + 3.05 spread at tier 2; 60.93 at tier 4), the market close as taker
(185.47 + 3.09; 108.19 at tier 4), a 5 BTC market buy was denied (USD 306,259.20 needed,
69,533.10 free) and a naked 0.1 BTC sell was denied.

## Fractional sizing (M4)

- **Helpers** (`src/core/position_sizing.py`, framework-free, Decimal in and out):
  `crypto_units_for_notional(notional, price, increment)` and
  `crypto_units_for_risk(risk, stop_distance, increment)` floor to the size increment with no
  minimum of one unit; with `min_base`/`min_usd` (the risk helper also needs `price` for the USD
  minimum) a size under Kraken's minimum order is 0, no order. `crypto_units(notional, price,
  pair)` reads the increment and `info["kraken"]` minimums of a spec-built pair. USD 10,000 of BTC
  at 78,000 is 0.12820512 BTC.
- **Strategies**: `sma_crossover` and `buy_and_hold` route a crypto pair (`is_crypto`) through
  `crypto_units` and submit `instrument.make_qty` (8 dp, exact: the size is already on the
  increment). Before M4, `sma_crossover` formatted a float to 8 dp (rounding up as often as
  down) and `buy_and_hold` floored to whole coins. A budget under the minimum places no order:
  `sma_crossover` warns once (`... is under the BTC/USD.KRAKEN minimum order`) and on stop
  logs the run's count (`37 order(s) dropped under the minimum order`). Equity, futures and FX
  sizing are unchanged.
- **`--units X`** (`src/services/sizing_overrides.py`, shared by the CLI, the MCP
  `submit_backtest`/`validate_config` and the run-time instrument check): for a coin with a spec,
  a decimal that is a positive multiple of its increment and at or above `min_order_base`, kept
  as an exact `Decimal` in the strategy config (and config hash; `0.25` and `0.250` hash alike).
  Else exit 2 (`--units: units 0.000000001 is not a multiple of the BTC size increment
  0.00000001`, `... under the Kraken BTC minimum order 0.00005`) or MCP `invalid_params`. FX still
  takes whole units only (an `int`, so FX configs and hashes are unchanged; `--units 1.5` on
  EURUSD exits 2). The MCP `units` argument takes a JSON integer or number (not a string).

**Smoke (2026-11-24, real `firstrate-crypto` bars, 1-day 2023-01-01 to 2024-12-31, USD
100,000, `sma_crossover` 10/20, tier 2, all fills taker).**

| Run | Trades | Quantities | Commission + spread (USD) | Final balance |
|-----|--------|------------|---------------------------|---------------|
| BTC, 10% notional | 18 | 0.14020419 to 0.40237530 BTC | 2,222.95 + 37.05 | 108,230.08 |
| ETH, 10% notional | 20 | 2.72974190 to 6.02195605 ETH | 2,419.77 + 60.52 | 100,813.30 |
| BTC, `--units 0.25` | 18 | 0.25000000 BTC each | 2,394.54 + 39.91 | 110,521.28 |
| ETH, `--units 0.25` | 20 | 0.25000000 ETH each | 144.59 + 3.63 | 100,048.56 |

Persisted `trades.quantity` keeps all 8 decimals; each run's trade commissions sum to the
snapshot's `costs_usd` and its P&L to the final balance less 100,000. One BTC `--units 0.25`
round trip (24,852.42 in, 22,369.40 out) pays 70.83 taker + 1.18 spread = 72.01, as charged.
Too small: `--units 0.00001` and `--units 0.000000001` exit 2 naming the minimum and the
increment; a USD 5 balance (a USD 0.50 budget, 0.00002 BTC) places no order, warns once and
reports 37 dropped orders on stop (final balance 5.00).

## Reporting, symbols and the dashboard (M5)

- **Figures**: `src/services/crypto/returns.py` (pure: quantity, USD notional, side-aware
  underlying return, USD P&L; run peak notional, peak exposure = notional / equity, maker,
  taker and spread costs, drop count) and `display.py` (the shared wording). `show`,
  `report summary|generate` (text/CSV/JSON, `crypto_` prefixed CSV keys, `crypto` JSON object
  with per-trade `coin_quantity`), `history` (`BTC (crypto)`), `compare` (Asset Class, Fee
  Tier, Peak Exposure, costs), MCP `get_run`/`compare_runs` (`asset_class: "crypto"`, a
  `crypto` object, a warning) and `export_results` (crypto trade columns) all read them.
  Equity, futures and FX output is byte-identical.
- **Drop count**: saved as `config_snapshot.crypto_pairs[<id>].orders_below_minimum` (no
  migration). A strategy starts it with `track_orders_below_minimum(cache, instrument_id)` on
  start and adds drops with `count_order_below_minimum(cache, instrument_id)`
  (`src.core.crypto_costs`); an untracked pair and runs before M5 report "not recorded".
- **Peak exposure** is exact for single-entry positions that span bars and an upper bound for a
  position that scales in (trade rows keep the largest quantity, not the fill history) or
  opens and closes within one timestamp (counted as held then).
- **Symbols**: `src/services/crypto/symbols.crypto_coin` (aliases from `coin_specs.yaml`):
  `BTC`, `BTCUSD`, `BTC/USD`, `BTC-USD`, `XBTUSD`, `XXBTZUSD`, `BTC/USD.KRAKEN` are `BTC`;
  `BTC/EUR`, `BTCUSDT`, `BTC/USD.BINANCE`, `DOGE/USD` are refused (CLI exit 2, MCP
  `unknown_crypto_pair`). `--spread-bps X` overrides the modelled spread.
- **Dashboard**: `templates/partials/crypto_panel.html` on the run page, a crypto branch in the
  trades table (`Quantity (coin)` at full precision, entry/exit at price precision, Notional $,
  Underlying %, P&L $), crypto export columns and a `CRYPTO` list badge.

**Check (2026-11-24, real runs)**: `--symbol BTC/USD.KRAKEN --fee-tier 3 --starting-balance
100000` 1-day 2023-2024: 18 trades, 0.14020419 to 0.4023753 BTC, taker 1,407.85 + spread 37.05,
final 109,045.18; `--symbol XBTUSD --starting-balance 5`: no trades, 37 drops saved and shown.

## Strategies: `riptide` and `bollinger_reversal` (M6)

The strategies submodule (`Trading-ntrader-strategies` PR #6, merge `32c8322`) sizes both in
fractional coins through the M4 helpers, rounds limit, stop and bracket prices at the pair's
precision (4 dp), counts orders under Kraken's minimum (M5) and runs long-only: Bollinger
skips bearish setups (warns once, logs the count on stop) and caps its risk size at what the
free USD buys; Riptide is long-only already. Configs:

- `configs/riptide_crypto.yaml`: the equity parameters on 1-day bars, USD 100,000, 10% per
  entry, `min_adv_dollars` USD 1,000,000 (single-venue Kraken volume, a day), `max_hold_days`
  10 **bars** (calendar days on 1-day bars; crypto trades 24/7), `fill_model: gap_aware`. For
  1-hour bars, copy it with `min_adv_dollars` about USD 40,000 (an hour) and `max_hold_days`
  in hours.
- `configs/bollinger_crypto.yaml`: 1-day bars by default (Allay, 2026-11-24), 1% risk of USD
  100,000, the FX/equity band, weekly SMA and ATR settings; weekly buckets are UTC ISO weeks.

**Exit timing (decision C24).** Riptide's green and time exits cancel the hard stop and send a
reduce-only market close beside it. The M3 gate counted the stop pending cancel against the
long and denied the close (`CRYPTO_SPOT_NO_SHORT`), so the long never closed. PR #6 worked
around it by sending the close once the cancel landed, which fills one bar late under
`gap_aware`. NTrader now lets a reduce-only close take a reduce-only stop's claim while its
cancel is pending (above); strategies PR #7 (`fix/crypto-exit-timing`) removes the
workaround, so crypto exits fill at the next bar's open, as on equities. Until #7 is merged
and pinned, the pinned #6 still waits for the cancel (correct, one bar late).

**Smoke (2026-11-24, real `firstrate-crypto` bars, USD 100,000, tier 2, `--no-persist`).**
Riptide 1-day and Bollinger 1-day: 2021-01-01 to 2025-12-31 (SOL data starts 2021-03-01).
Riptide 1-hour: BTC 2024 with `min_adv_dollars` 40,000 and `max_hold_days` 10 (bars). Exit
lag is market-exit fill bar minus signal bar. No order was denied in any run and no position
was left open.

| Run | Strategy commit | Trades (stop / other exits) | Coin sizes | Fees incl. spread (USD) | Exit lag (bars) | Final balance |
|---|---|---|---|---|---|---|
| Riptide BTC 1d | #6 `32c8322` (pinned) | 21 (4 / 17) | 0.08814773 to 0.45953774 | 1,922.82 | 1-2 | 96,571.00 |
| Riptide ETH 1d | #6 | 23 (3 / 20) | 2.42285156 to 6.60056770 | 2,132.92 | 1-2 | 96,876.00 |
| Riptide SOL 1d | #6 | 32 (2 / 30) | 45.14680839 to 505.15255607 | 3,089.38 | 2 | 104,811.85 |
| Riptide BTC 1h 2024 | #6 | 10 (2 / 8) | 0.09927447 to 0.22444903 | 923.50 | 2 | 99,649.29 |
| Riptide BTC 1d | #7 `163122b` | 24 (4 / 20) | 0.08814773 to 0.45953774 | 2,203.02 | 1 | 96,980.51 |
| Riptide ETH 1d | #7 | 25 (3 / 22) | 2.42285156 to 6.60056770 | 2,324.85 | 1 | 97,648.64 |
| Riptide SOL 1d | #7 | 33 (2 / 31) | 45.14680839 to 505.15255607 | 3,173.39 | 1 | 102,966.60 |
| Riptide BTC 1h 2024 | #7 | 11 (2 / 9) | 0.09927447 to 0.22444903 | 1,015.99 | 1 | 99,634.99 |
| Bollinger BTC 1d | #6 and #7 (identical) | 8 (4 stops) | 0.12932707 to 0.64617253 | 1,141.21 | - | 102,858.79 |
| Bollinger ETH 1d | #6 and #7 | 12 (7 stops) | 1.33009802 to 16.88333614 | 1,382.12 | - | 101,617.88 |
| Bollinger SOL 1d | #6 and #7 | 8 (7 stops) | 23.17573831 to 380.10425716 | 568.43 | - | 94,431.55 |

Bollinger skipped 30 (BTC), 21 (ETH) and 20 (SOL) bearish setups as long-only. With #7 every
Riptide exit fills one bar after its signal (the equity next-open rule); a few more trades
fit in because each long closes a bar sooner and Riptide holds one position at a time. Unchanged by M6 (fill events identical
before and after the pin bump and gate fix): Riptide AAPL 2019-2024 (20 trades) and the
default `configs/riptide.yaml`; Riptide EURUSD (16) and USDJPY (24) and Bollinger EURUSD (92)
and USDJPY (104) on `configs/*_fx.yaml`; Riptide ES (7) and Bollinger ES (36) 2019-2024 with
`--contracts 1`; Bollinger AAPL 2019-2024 (one entry, still open at the end).

## Account model: the M1 spike (decision C8)

`tests/integration/core/test_crypto_engine_acceptance.py` (run `--forked`) settled it:
**model A**, a `MARGIN` venue with `base_currency=USD`, `default_leverage=1`, instrument
`margin_init=1`, **plus the margin gate**.

- A 0.128 BTC market order fills at `0.12800000`; P&L lands in the single USD balance.
- With a prototype gate (shared `MarginGate`, `qty x price` requirement, no-short rule) the
  bracket scenario runs and the over-cash buy (`MARGIN_EXCEEDS_FREE_BALANCE`) and naked sell
  (`CRYPTO_SPOT_NO_SHORT`) are denied. **Without the gate nothing is enforced**: Nautilus skips
  risk checks for margin accounts, so the USD 156k buy fills on USD 10k and the short opens.
- `CASH` is rejected: single-currency refuses the `CurrencyPair`; multi-currency leaves market
  orders unchecked in a bar-only run, allows shorts, stops the run on a negative balance and
  leaves the books inconsistent.
- Under `HEDGING` a `SELL` without a position id is a new short, so M3's rule allows a
  parentless `SELL` only against an open long it does not oversell (shipped in M3, above).
- Bar volume never caps fill size; the part beyond the bar volume fills one increment
  (0.0001) worse. Bar volume must carry precision 8 (the size precision) or the run stops.

Details and the numbers pinned: `design.md` section 1, "Spike result".
