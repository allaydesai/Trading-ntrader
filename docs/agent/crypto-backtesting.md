# Crypto backtesting: coins, instruments, account model

Spot crypto on Kraken, planned in `openspec/changes/add-crypto-backtesting/` (milestones
M1-M7; decisions C1-C23 in `design.md` section 13). **Status: M1-M3 implemented** (coin
specs, instruments, catalog dispatch, engine spike; the FirstRate importer and the
`firstrate-crypto` catalog; the account, Kraken fees and the spot gate, below). Crypto
backtests run (`backtest run --symbol BTC --catalog firstrate-crypto`), charged at the Kraken
Pro tier and held to spot rules. Not yet: floor-to-increment sizing (M4; `sma_crossover`
already sizes BTC fractionally through its `size_precision` branch, which rounds instead of
flooring), crypto reporting (M5), the `BTC/USD` symbol forms and `--spread-bps` (M5), strategy
ports (M6) and the Kraken data path (M7).

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
  free=2152.42 USD`. A parentless, non-reduce-only `SELL` must close an open long by position
  id and not oversell it (net of other working sells on it), else `CRYPTO_SPOT_NO_SHORT: SELL
  0.10000000 BTC/USD.KRAKEN would go short; ...`. Bracket legs and reduce-only exits pass;
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
