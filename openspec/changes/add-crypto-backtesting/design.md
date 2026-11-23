# Design: spot crypto backtesting

Values marked **UNVERIFIED** could not be confirmed from a public primary source on
2026-11-23, or public sources disagree. Each one is flagged in `coin_specs.yaml` with a
`source` and an `as_of`, as the FX and futures specs are. Allay confirms them (IBKR Client
Portal contract details and commission tier, Kraken account fee tier) before the milestone that
first *charges* them (M3) merges.

## 0. Survey (2026-11-23)

### What exists today

| Area | State | Crypto impact |
|---|---|---|
| `AssetClass` (`src/models/catalog.py`) | `CRYPTO` member exists; `import_data.py` maps `--asset-class crypto` | no parser registered, so it exits 2 |
| Instruments | `build_instrument` (`firstrate/backtest_loader.py:104`): `FUTURES` and `FX` have builders, everything else goes to `build_equity` | a `CRYPTO` row would become an `Equity` |
| Kraken data path | `kraken_client.py` (461 lines): `KrakenHistoricalClient.fetch_bars` pages the **Kraken Futures Charts API** with `to_kraken_charts("BTC/USD") = "PF_XBTUSD"` (perpetual), then `build_currency_pair` from **spot** `AssetPairs` metadata; `_backtest_helpers._load_kraken_data` builds a fallback `CurrencyPair` (size precision 8, maker 0.16% / taker 0.26%) | perp prices in a spot instrument; fees on the instrument are never charged (below) |
| Kraken settings | `KrakenSettings` (`config.py:164`): key/secret, rate limit, default maker/taker fee | the defaults (0.16/0.26%) predate Kraken's current schedule |
| `CurrencyPair` helpers | `is_fx` (`fx_rates.py:51`) and `is_fx_pair` (`position_sizing.py:53`) key on `info.asset_class == "FX"`, so Kraken crypto pairs deliberately take the equity path | crypto needs a sibling `is_crypto`, never the FX rate path |
| Venue | `add_backtest_venue`: `MARGIN`, `HEDGING`, `starting_balances=[USD]`; FX venues add `base_currency=USD` and `default_leverage=1` | crypto needs an unlevered, long-only account (section 3) |
| Cost models | `venue_cost_models` (`futures_costs.py:186`): FX gets `FxCommissionModel`, futures `FuturesCommissionModel`, **everything else the equity per-share model** | crypto would be charged USD 0.005 per coin, USD 1 minimum |
| Margin | `MarginGate` (`margin_gate.py`) with `FuturesRequirement` and `FxRequirement` strategies | add a `CryptoSpotRequirement` and a short-sale rule |
| Sizing | core `position_sizing.py` (`units_for_notional`, `whole_contracts`, `whole_units`, FX helpers); submodule `_sizing.py` returns `int`, equities `max(int(raw), 1)`; strategies submit `Quantity.from_int` | **1 BTC for a USD 10k budget**; fractional sizing is the core change (M4, M6) |
| Results / DB | `trades.quantity`, prices, P&L are `Numeric(20, 8)`; `mark_to_market` accepts any USD-quoted linear instrument and cuts the day at **UTC midnight** | fractional coin quantities fit; no migration; the UTC day already matches crypto |
| Metrics | `TRADING_DAYS = 252` (`mark_to_market.py:26`), `metrics.py` assumes 252; Nautilus' own stats are 252-day | crypto equity has 365 daily points a year, so Sharpe/Sortino/volatility are mis-annualised by `sqrt(252/365)` (section 9) |
| Parsers | `FirstRateCsvParser` reads the same `YYYY-MM-DD HH:MM:SS,O,H,L,C,V` layout but localises to `America/New_York` (`firstrate_csv_parser.py:29`) | crypto files are UTC; reusing it would shift every bar 4-5 h |
| Nautilus 1.220 adapters | `binance`, `bybit`, `coinbase_intx`, `okx`, `dydx`, `hyperliquid`, `bitmex`, `interactive_brokers`, ...; **no Kraken adapter** | live Kraken would need a custom adapter |
| Nautilus IB adapter | `VENUES_CRYPTO = ["PAXOS"]`; `RE_CRYPTO` parses `BTC/USD`; `parse_crypto_contract` returns a **`CryptoPerpetual` with `is_inverse=True`**, margin and fees 0; PAXOS `LAST` bars map to IB `AGGTRADES` | id scheme `BTC/USD.PAXOS` is live-native, but the live instrument model is wrong for spot (section 12) |
| IBKR (MCP `search_contracts`) | `BTC` sec type `CRYPTO`, exchange `PAXOS`, conId 479624278; `SOL` `CRYPTO`/`PAXOS`, conId 766137558 (ETH not looked up) | confirms venue naming and that SOL is tradable at IB |
| Instruments in Nautilus | `CurrencyPair` (spot, base/quote, fractional `size_precision`, `maker_fee`/`taker_fee`), `CryptoPerpetual` (linear or inverse, settlement currency, funding via custom data), `CryptoFuture` (dated) | spot = `CurrencyPair` (section 1) |

### Crypto data on the VPS (read-only)

Raw: `/srv/market-data/raw/crypto/` (in `MANIFEST.sha256`, about 1.0 GB). There is **no
crypto catalog**: `catalogs/` holds `firstrate-etf`, `firstrate-futures`, `firstrate-fx` and
`firstrate-stocks`. Nothing Kraken-fetched is persisted under `data/catalogs`.

| Coin | Range (UTC) | Rows 1min / 5min / 30min / 1h / 1d |
|---|---|---|
| BTC | 2013-04-01 to 2026-05-02 23:59 | 6,129,198 / 1,334,755 / 228,603 / 114,579 / 4,780 |
| ETH | 2016-03-11 to 2026-05-02 23:59 | 4,996,580 / 1,029,089 / 176,326 / 88,542 / 3,698 |
| SOL | 2021-03-01 to 2026-05-02 23:59 | 2,635,929 / 539,644 / 90,668 / 45,336 / 1,889 |

1-minute files are 375 MB (BTC), 298 MB (ETH), 154 MB (SOL).

- **Format**: headerless CSV, the **equity layout**: intraday `YYYY-MM-DD HH:MM:SS,O,H,L,C,V`,
  daily `YYYY-MM-DD,O,H,L,C,V`. No open interest.
- **Timezone: UTC.** FirstRate's FAQ says all data is US Eastern "except for crypto data
  which is in UTC". Confirmed in the data: every one of the 24 hours appears, the US DST
  Sundays (2021-03-14, 2024-03-10, 2024-11-03) each have their 01:00-03:00 bars exactly once,
  and rebuilding the daily bars from the hourly bars with a plain midnight cutoff matches the
  last 1,000 daily bars of each coin **1000/1000** (OHLC); an ET-midnight cutoff matches 0.
  Bars are stamped at the **bar open** (same convention as the other FirstRate series).
- **24/7**: all seven weekdays carry the same number of bars (BTC hourly 16,338-16,383 per
  weekday). No session filtering applies.
- **Gaps**: minutes with no trade are absent, not zero-filled. BTC 1-minute has 279,508 gaps
  (mostly 2013-2016, max 7 h 10 min); BTC hourly has 104 gaps (max 8 h); ETH hourly 125
  (one of 7 days 21 h in early 2016); ETH daily one 8-day gap; SOL has none at 1 h/1 d.
- **Prices**: trailing zeros stripped. Widest significant precision: **4** at 1 h/1 d for all
  three coins (2021-2022), **5** at 1 minute (e.g. BTC 2016-08-03 `534.96827`). From 2023 the
  data is 2 decimals for BTC/ETH. No row breaks the OHLC rules; all prices > 0.
- **Volume**: coin units (BTC, not USD), up to 8 decimals; FirstRate's dataset page says the
  same. **From 2026-03-21 00:00 UTC volume is rounded to whole coins** in every file (the last
  fractional hourly volume is 2026-03-20 23:00), so recent BTC 1-minute bars carry volumes like
  `2` and some `0`. BTC 1-minute has 1,176 zero-volume rows.
- **Source venue: UNVERIFIED.** FirstRate's crypto page has a placeholder for the exchange.
  Daily BTC volume (about 14k BTC on 2026-05-01) suggests a single venue, not a consolidated
  tape. The quote is **USD** (FirstRate: "USD unless the ticker says otherwise"), not USDT.
- **No bid/ask**: one trade-price series per coin; the spread is modelled (section 4).

Source proposal: these three coins are enough for M1-M7. More coins come from more FirstRate
files (same layout, same parser) or, later, IB `AGGTRADES`/`MIDPOINT` history once
`ibkr_data_provider` learns `CRYPTO`. The Kraken path (M7) stays for ad-hoc pairs.

## 1. Instrument model (M1)

### Spot only: why `CurrencyPair`

A spot coin is a currency exchange: buying 0.1 BTC for USD 7,800 moves two balances, P&L is
realised in the quote currency, and there is no expiry, funding or liquidation. Nautilus'
`CurrencyPair` models exactly that (FX already uses it), with a fractional
`size_precision`/`size_increment`. `CryptoPerpetual` adds funding, margin and (for inverse
contracts) settlement in the coin; `CryptoFuture` adds expiry. Neither fits spot data, and the
data on the VPS is spot. Perps are decision C1.

### `src/services/crypto/coin_specs.yaml`

Same discipline as `pair_specs.yaml`: `schema_version`, `specs_as_of`, pydantic with
`extra="forbid"`, and `CryptoSpecError` naming the file and the dotted path. Per coin (key =
FirstRate ticker, e.g. `BTC`):

```yaml
BTC:
  base: BTC
  quote: USD
  price_precision: 4          # data precision (FirstRate 1h/1d max); 1-minute rows rounded
  size_precision: 8
  size_increment: "0.00000001"
  typical_spread_bps: "2"     # UNVERIFIED modelled full bid/ask spread
  data: firstrate             # or none (spec-only)
  venues:
    PAXOS:                    # default (decision C2)
      min_order_usd: "1"      # UNVERIFIED (IB contract details minSize x price)
      min_tick: "0.01"        # UNVERIFIED (IB minTick); live rounding only
      ib: {sec_type: CRYPTO, symbol: BTC, currency: USD, exchange: PAXOS, local_symbol: BTC.USD, con_id: 479624278}
    KRAKEN:
      min_order_base: "0.00005"   # UNVERIFIED (Kraken AssetPairs ordermin)
      kraken: {rest: XXBTZUSD, ws: BTC/USD}
fee_profiles:                 # one schedule per venue, not per coin
  PAXOS:                      # IBKR crypto via Paxos/Zero Hash, tier 1 (<= USD 100k/month)
    kind: percent_with_min
    rate_bps: "18"            # published (IBKR "Commissions Cryptocurrencies"); account tier UNVERIFIED
    min_per_order_usd: "1.75"
    max_pct_of_value: "1.0"   # the minimum is capped at 1% of trade value
    as_of: 2026-11-23
  KRAKEN:                     # Kraken Pro spot, tier 1
    kind: maker_taker
    maker_bps: "25"           # UNVERIFIED: en-US page 0.25/0.40; Kraken Pro pages 0.40/0.80
    taker_bps: "40"           # UNVERIFIED
    as_of: 2026-11-23
```

Cross-field checks: base != quote; the key is the base; `size_increment` decimals fit
`size_precision`; quote must be `USD` (decision C6); each venue's block agrees with base/quote;
every `fee_profiles` key is a venue some coin lists. Shipped coins: BTC, ETH, SOL.

### `build_crypto_pair()` (`src/services/crypto/instrument_builder.py`)

A Nautilus 1.220 `CurrencyPair`:

- `instrument_id = BTC/USD.PAXOS` (decision C2). That is what the IB adapter's `RE_CRYPTO`
  parses for `PAXOS`, so a backtest bar type names the same instrument a live subscription
  would. `raw_symbol = BTC.USD` (IB `localSymbol`). A `KRAKEN` profile builds
  `BTC/USD.KRAKEN` with `raw_symbol = XBT/USD` for the Kraken path (M7).
- `price_precision` from the bars when given (Nautilus rejects a mismatch), else the spec.
  `price_increment = 10^-precision` (the data grid; the venue tick stays in `info` for live).
- `size_precision = 8`, `size_increment = 1e-8` (spec-driven).
- `margin_init = margin_maint = 1` (100% of notional: unlevered; decision C8).
  `maker_fee = taker_fee = 0` (the venue fee model charges; section 4).
- `info`: `asset_class = "CRYPTO"`, `venue_profile`, `typical_spread_bps`, minimums, the
  fee profile terms and `as_of`, the IB or Kraken block.

`build_instrument` routes `CRYPTO` (case-insensitive) to it. Equity, futures and FX paths stay
byte for byte as they are. `is_crypto(instrument)` (core, duck-typed like `is_fx_pair`)
is the one predicate every later milestone keys on.

### Catalog row (no migration, decision C11)

`ticker = BTC`, `nautilus_id = BTC/USD.PAXOS`, `asset_class = CRYPTO`, `exchange = PAXOS`,
`currency = USD`, `tick_size` = price increment, `multiplier = 1`, `contract_kind = NULL`.

### Engine-acceptance spike (M1 gate for the whole plan)

A forked integration test proves, before M2-M7 are built on it:

1. A venue with `base_currency=USD` accepts `BTC/USD.PAXOS` with 4-decimal `LAST` bars and a
   0.12800000 BTC market order fills at that quantity (no rounding to 1).
2. **Account model** (decision C8). Two candidates are run side by side on the same scenario
   (buy 0.1 BTC with a bracket: a resting stop-loss and a take-profit `SELL`, both 0.1 BTC;
   then a second buy that would exceed the cash):
   - **A (recommended): `MARGIN`, `default_leverage=1`, `margin_init=1`.** The bracket is
     accepted, the second buy is denied by the gate (prototype), a short `SELL` with no position
     is denied, and P&L is USD.
   - **B: Nautilus `CASH` account.** Nautilus' `RiskEngine` enforces cash natively, but with a
     single-currency cash account it checks cumulative **SELL notional against free USD**
     (`risk/engine.pyx` around line 787), and a multi-currency one locks base balance per open
     sell, so a closing stop plus a take-profit may be denied. The spike pins what actually
     happens.
   If A passes, it is the decision of record and B is documented as rejected. If A fails, B
   (or a fix) is re-planned before M2 merges.
3. A partial fill against a 1-minute bar with tiny or zero volume (the 2026 rounded volumes)
   is measured and pinned, so M2/M6 know whether bar volume caps fill size (decision C15).

## 2. Importer (M2)

`FirstRateCryptoParser` (`src/services/firstrate/parsers/firstrate_crypto_parser.py`),
registered for `AssetClass.CRYPTO`, and `CryptoImportService`
(`src/services/firstrate/crypto_import_service.py`), sharing helpers with the FX/futures pair
(chunked read, footer verification, `.import-incomplete` marker, classifier).

- **Layout**: the equity layout (single datetime column; 5-field daily rows). Discovery accepts
  `{COIN}_full_{tf}.txt` flat or one directory down. A coin without a spec fails that file only.
- **Timestamps**: parsed as **UTC** (no localisation, no DST rules), stamped at bar open. A
  daily row dated `D` is stamped `D 00:00 UTC` (decision C10).
- **Precision**: prices are rounded half-even to the spec's `price_precision` (4). The rows whose
  value changed by more than 1e-9 are counted per file and reported as a warning (expected:
  1-minute only). Volume is stored at 8 decimals.
- **Validation**: prices > 0, OHLC consistency, volume >= 0 (zero valid). Bars stored as
  `{ID}-{step}-{agg}-LAST-EXTERNAL` (trades, matching IB `AGGTRADES` live).
- **Warnings, not drops**: gaps longer than 10 bars, the volume-rounding regime change (first
  bar whose volume and all later volumes are integers), zero-volume bars. Nothing is filled
  forward.
- **CLI**: `import --format firstrate --asset-class crypto --catalog firstrate-crypto <dir>`,
  all five timeframes by default, `--dry-run` showing per-coin ranges. The 6.1M-row BTC
  1-minute file streams in chunks (peak RSS measured in the manual task; target < 1.5 GB).

## 3. Account, leverage and shorting (M3)

- **Account** (decision C8, option A unless the spike says otherwise): crypto venues are
  `MARGIN` with `base_currency=USD` and `default_leverage=1`, like FX. P&L, commission and
  balance are USD natively because every shipped pair is USD-quoted: **no rate feeder** is
  needed. `needs_conversion` stays FX-only. A venue mixing crypto with FX or equities is refused
  (a `PAXOS` venue only lists crypto).
- **No leverage**: `CryptoSpotRequirement.required_initial(qty, price) = qty x price` (USD). The
  shared `MarginGate` rules (new exposure, HEDGING closing credit, venue-wide reservations,
  quantity-increase modify) apply unchanged; over-allocation is denied
  `MARGIN_EXCEEDS_FREE_BALANCE` (existing prefix). Commission headroom is not reserved (a fill
  can take the balance a few dollars below zero; reported, not denied; same as FX).
- **No shorting**: an order is denied `CRYPTO_SPOT_NO_SHORT` when the net position after it
  (counting other working orders on the same side) would be negative. Reduce-only exits and
  bracket legs that only close are allowed. Futures and FX reasons are unchanged.
- **Results**: `results_extractor`, `backtest_persistence` and `mark_to_market` already accept a
  USD-quoted linear `CurrencyPair`; M3 adds tests that pin fractional quantities through the
  ledger, the `trades` rows and `_check_against_accounts`. `config_snapshot` gains
  `account_currency: "USD"` and `crypto_pairs` (per instrument: venue profile, fee terms, spread,
  precision, `as_of`), declared on `StrategyConfigSnapshot` and omitted when unset.

## 4. Costs (M3)

### Commission (decision C3)

`CryptoCommissionModel(FeeModel)` returns USD `Money`, per the instrument's fee profile:

```
trade_value = fill_qty x fill_px                         # USD-quoted
PAXOS:  commission = min(max(rate_bps/1e4 x value, min_per_order_usd), max_pct x value)
KRAKEN: commission = (maker_bps if liquidity_side == MAKER else taker_bps)/1e4 x value
        (x the run's cost-stress multiplier in both cases)
```

The PAXOS minimum is **per order**, so the model keeps the commission charged per
`client_order_id` and charges later partial fills only the top-up (the FX model's mechanism,
shared). Examples at 18 bp / USD 1.75 / 1%: USD 9,984 of BTC costs USD 17.97; USD 500 costs
USD 1.75; USD 100 costs USD 1.00 (the 1% cap). Kraken 40 bp taker on USD 9,984 is USD 39.94.

Sources: IBKR "Commissions Cryptocurrencies" (Americas): 0.18% up to USD 100k/month,
0.15% to USD 1M, 0.12% above; USD 1.75 minimum, no more than 1% of trade value; no added
spreads or markups; USD 0.15/month Paxos account fee (not modelled). Kraken Pro fee pages:
tier 1 0.40% maker / 0.80% taker on most regional pages, but the en-US fee schedule shows
0.25% / 0.40% (**UNVERIFIED** which applies to Allay's account; a tier change on 2026-07-09
also counts assets on platform). The repo's `KRAKEN_DEFAULT_*_FEE` defaults (0.16/0.26%) are
outdated; M7 reads the spec instead.

### Spread and slippage (decision C4)

Single trade-price series, so the spread is **modelled**, exactly as FX does: each fill is
charged `qty x price x typical_spread_bps / 2 / 1e4` (a half-spread per side) as a separate
component of the same fee model, scaled by cost stress, overridable with `--spread-bps` or
`CRYPTO_SPREAD_BPS_<COIN>`. Defaults (**UNVERIFIED**, full spread): BTC 2 bp, ETH 3 bp,
SOL 5 bp. IBKR's "no added spread" means no broker markup, not a zero bid/ask on the Paxos
book. Both components are recorded separately in `config_snapshot.crypto_pairs[...].costs_usd`
and in reports.

## 5. Position sizing (M4)

Units are **base coin units**, fractional (BTC qty 0.128 = 0.128 BTC).

Framework-free helpers in `src/core/position_sizing.py` (USD quote only, so no rate argument):

- `crypto_units_for_notional(notional_usd, price, size_increment)` =
  `floor_to_increment(notional / price)`.
- `crypto_units_for_risk(risk_usd, stop_distance, size_increment)` =
  `floor_to_increment(risk / stop_distance)`.
- **No minimum of 1**; a result below the venue minimum (`min_order_usd` or `min_order_base`)
  becomes 0 (no order) and is counted once per run in the run warnings (decision C13).
- Quantities are passed to Nautilus as `instrument.make_qty(units)`, never `Quantity.from_int`.

`sma_crossover` and `buy_and_hold` route crypto through these. `backtest run --units X` for
crypto accepts a decimal > 0 that is a multiple of the size increment (FX keeps int >= 1;
futures `--contracts` unchanged); MCP `submit_backtest`/`validate_config` follow the same rules.

## 6. Ticks and fills

The price increment is the data grid (10^-4). Venue ticks (IB `minTick`, Kraken
`tick_size`, UNVERIFIED) are stored for live. `GapAwareFillModel` is unchanged; with 24/7 data,
gaps are rare (exchange outages, sparse early years). Fill size against bar volume is pinned by
the spike (section 1, item 3) and documented; the importer does not inflate volume.

## 7. Sessions and the trading day

- 24/7, UTC. The trading day is the **UTC calendar date**; daily bars open at 00:00 UTC; the
  existing `daily_equity` already cuts at UTC midnight, so no crypto-specific equity cut is
  needed (unlike FX's 17:00 New York).
- `crypto_trade_date(ts_ns)` in `src/services/crypto/sessions.py` (trivial, but one place
  strategies and reports import, so the rule can change in one spot).
- Weekends are trading days. Strategy parameters counted in days (`max_hold_days`, ADV
  windows) mean calendar days on crypto; configs say so.

## 8. Strategy compatibility (M6, submodule PR + pointer bump)

| Strategy | Problem on crypto | Fix |
|---|---|---|
| both | `_sizing.notional_quantity`/`risk_quantity` return `int` with an equity min of 1, and orders use `Quantity.from_int` | crypto branch keyed on `is_crypto`, fractional units (section 5), `instrument.make_qty` |
| riptide | `_compute_limit_price`/`_compute_stop_price` quantize to `0.01` for non-FX | for crypto, quantize at `instrument.price_precision` (the FX rule); equities unchanged |
| riptide | `_is_liquid` uses `close x volume`: correct USD volume for coin-unit volume, but single-venue (much lower than consolidated) and integer-rounded after 2026-03-21 | keep the check; `configs/riptide_crypto.yaml` sets a crypto-sized `min_adv_dollars`; README notes the regime change |
| riptide | "MOO" and `max_hold_days` assume weekday sessions | on crypto, the next bar open is the next UTC day, weekends included; no code change, but `max_hold_days` is documented as calendar days and the crypto config sets it explicitly |
| riptide | long-only | matches the no-short gate |
| bollinger_reversal | `Price.from_str(f"{x:.2f}")` brackets | `f"{x:.{precision}f}"` at instrument precision for crypto (FX rule) |
| bollinger_reversal | weekly SMA buckets by UTC ISO week | correct for crypto (UTC); a test pins it |
| bollinger_reversal | short entries, if enabled | denied `CRYPTO_SPOT_NO_SHORT` by the gate; config runs long-only on crypto and logs once |
| both | | configs `configs/riptide_crypto.yaml`, `configs/bollinger_crypto.yaml`; smoke runs BTC/ETH/SOL 1-day and 1-hour, 2021-2025 |

## 9. Metrics: annualisation

`TRADING_DAYS = 252` (`mark_to_market.py`) and `metrics.py`'s `periods_per_year = 252` assume
weekday sessions. Crypto daily equity has a point for every calendar day, so annualised
volatility, Sharpe and Sortino would be understated by `sqrt(252/365)` (about 0.83x) and any
"per year" figure computed from bar counts is off by 365/252. Decision C20: crypto runs
annualise on **365**, chosen by asset class of the run's instruments and recorded in
`config_snapshot` (`periods_per_year`). Equity, futures and FX keep 252 (FX trades 5 days).
Nautilus' own `returns_stats` labels ("Sharpe Ratio (252 days)") stay as Nautilus reports them;
NTrader's headline metrics come from `mark_to_market` (the M3 tests pin which is shown).

## 10. Reporting, dashboard and CLI (M5)

`src/services/crypto/returns.py` (pure, like `fx/returns.py`):

| Figure | Formula |
|---|---|
| quantity | coin units, shown to the instrument's size precision with trailing zeros trimmed |
| notional USD | `quantity x entry` |
| return on the underlying | side-aware `(exit - entry) / entry` |
| P&L USD | stored `profit_loss` |
| run: peak exposure | `max concurrent notional / equity` (<= 1 by construction; shows idle cash) |
| run: costs split | commission vs modelled spread, USD; fee profile name |

**Headline (decision C19)**: account return on USD equity, Sharpe/drawdown annualised on 365.
Return on the underlying and costs alongside. Every crypto report states "spot, unlevered,
long-only; fee profile X; spread modelled".

- CLI `show`/`report`/`history`: a crypto block (`BTC/USD (crypto, PAXOS)`), fractional
  quantities. Equity, futures and FX output byte-identical.
- MCP `get_run`/`compare_runs`/`export_results`: `asset_class: "crypto"`, a `crypto` object,
  crypto trade columns only when a crypto run is exported.
- `backtest run`: `--symbol BTC|BTCUSD|BTC/USD` resolves through the catalog row to
  `BTC/USD.PAXOS`; `--timeframe 1-HOUR` maps to `1-HOUR-LAST` (`price_type_for(CRYPTO)` =
  `LAST`); `--units X` decimal; `--fee-profile paxos|kraken` (default from the spec, decision
  C3); `--spread-bps`.
- **Dashboard (needs a FROZEN_VIEWS ruling, decision C12)**: the FX ruling's file set with a
  crypto branch on each: `src/api/models/backtest_detail.py` (`view.crypto`), new
  `templates/partials/crypto_panel.html`, `templates/backtests/detail.html` (include),
  `src/api/ui/backtests.py` (Run form bar type and decimal units),
  `templates/partials/trades_table.html` (Quantity with decimals, Notional $, Underlying %,
  P&L $), `src/api/rest/trades.py` (export), `src/api/models/backtest_list.py` and
  `templates/backtests/list_fragment.html` (`CRYPTO` badge), `src/cli/commands/compare.py`.
  Equity, futures and FX pages stay byte-identical against goldens captured before the change.

## 11. Kraken path correction (M7, decision C16)

- `to_kraken_charts` returns the perpetual symbol `PF_XBTUSD`. The Kraken Charts API also
  serves spot series (**UNVERIFIED**: tick type and symbol format to be confirmed against the
  live API in M7's first task); Kraken's spot REST `OHLC` endpoint returns only the most recent
  720 candles per interval. M7 switches to the spot series if the Charts API has it; otherwise
  `--data-source kraken` refuses a range older than the REST window with a clear message.
  It never again wraps perpetual prices in a spot instrument.
- The fetched instrument is built by `build_crypto_pair(get_coin_spec(base), venue="KRAKEN")`
  when the coin has a spec (precision from the bars), else the current fallback path with fees
  from the `KRAKEN` profile, and the venue gets `CryptoCommissionModel`.
- `KRAKEN_DEFAULT_MAKER_FEE`/`TAKER_FEE` remain as overrides; their defaults move to the spec.
- Code moves out of `kraken_client.py` and `_backtest_helpers.py` so neither grows.

## 12. Live-trading compatibility notes (not implemented here)

- **Ids line up for IB**: `BTC/USD.PAXOS` is what the IB adapter parses and subscribes, and
  PAXOS `LAST` bars map to IB `AGGTRADES`, so backtest bar types work live unchanged.
- **But the IB adapter's instrument is wrong for spot**: `parse_crypto_contract` builds an
  inverse `CryptoPerpetual` (P&L settles in BTC, margin 0). The live path must replace it with
  the spec-built `CurrencyPair` (or patch the provider) before any crypto session; otherwise
  `LiveAccountView` (which refuses non-USD P&L) and the trade recorder would see BTC P&L.
- **Orders**: IB crypto order types and time-in-force are restricted (**UNVERIFIED**: market by
  cash quantity, limit, stop-limit; IOC/GTC/minutes TIF). Stop-market brackets may not be
  accepted live; the live effort must check Riptide/Bollinger order kinds against them. Round to
  `min_tick`, respect `min_order_usd` and the size increment. No shorting (account permission).
- **Sessions**: 24/7, but the IB Gateway's daily restart interrupts the feed;
  `use_regular_trading_hours` must be False for `CRYPTO`.
- **Kraken live**: Nautilus 1.220 has no Kraken adapter, so live Kraken means writing one (or
  using a different engine); out of scope here, and the reason the default venue is `PAXOS`.
- **Guard lists**: any live crypto change touches `src/core/live_*` and the
  `NODE_FACING_MODULES`/`EMITTED_*` lists (`docs/agent/guard-lists.md`).
- `ibkr_venue_provider.sec_type` and `ibkr_data_provider` need `CRYPTO`; that is also the route
  to IB historical crypto bars.

## 13. Decisions needed from Allay

Each row is a recommendation, pending Allay's approval. UNVERIFIED values are proposed working
defaults; M3 does not merge until the fee-related ones are confirmed or accepted as defaults.

| # | Decision | Recommendation | Status |
|---|---|---|---|
| C1 | Spot only, or also perps/futures | **Spot only** (`CurrencyPair`); perps (`CryptoPerpetual` + funding) as a separate later change; CME MBT/MET via the futures path if wanted | pending |
| C2 | Venue / instrument id | **`BTC/USD.PAXOS`** (IB-native, the live broker in use); `BTC/USD.KRAKEN` kept for Kraken-fetched data; catalog `ticker = BTC` | pending |
| C3 | Fee model | **IBKR Paxos default**: 18 bp, USD 1.75 min per order, capped at 1% of value (published; confirm account tier); **Kraken** profile selectable with `--fee-profile kraken` (0.25/0.40% vs 0.40/0.80% UNVERIFIED) | pending |
| C4 | Spread | modelled half-spread per side: BTC 2 / ETH 3 / SOL 5 bp full spread (UNVERIFIED), `--spread-bps` override | pending |
| C5 | Data source, symbols, bar sizes | **FirstRate BTC, ETH, SOL, all five timeframes** into `firstrate-crypto`; smoke and strategy configs on 1-day and 1-hour; source venue UNVERIFIED (ask FirstRate if it matters) | pending |
| C6 | Quote currency | **USD only** (the data and PAXOS are USD); USDT/USDC and coin-quoted pairs out of scope | pending |
| C7 | P&L / reporting currency | **USD** account; no conversion needed for USD quotes; non-USD quotes refused at spec load | pending |
| C8 | Leverage / margin for spot | **Unlevered, long-only**: `MARGIN` + `base_currency=USD` + `margin_init=1` + gate (`CRYPTO_SPOT_NO_SHORT`); fall back to a Nautilus `CASH` account only if the M1 spike rejects this | pending |
| C9 | Funding rates | not applicable to spot; the perps change must source Kraken/other funding history first | pending |
| C10 | Day / daily bar timestamp | UTC: daily bar at `D 00:00 UTC` (bar open), trade date = UTC date, equity cut at UTC midnight | pending |
| C11 | Schema | **no migration**: fractional quantities fit `Numeric(20, 8)`; terms in `config_snapshot.crypto_pairs` | pending |
| C12 | Dashboard | pre-approve a FROZEN_VIEWS regeneration for exactly the section 10 files (FX ruling set plus `crypto_panel.html`) | pending |
| C13 | Sizing unit | fractional coin units floored to `size_increment`, no minimum of 1, below-minimum orders dropped and counted; decimal `--units` | pending |
| C14 | Price precision | fixed 4 decimals per coin (the 1h/1d data maximum); 1-minute 5-decimal rows rounded half-even and counted | pending |
| C15 | Volume | store as-is (coin units, 8 dp); warn on the 2026-03-21 whole-coin rounding; no volume inflation; spike pins fill behaviour | pending |
| C16 | Kraken path | fix to spot series or refuse old ranges (M7); never perp prices in a spot instrument | pending |
| C17 | Coin universe | BTC, ETH, SOL (the data on the VPS) | pending |
| C18 | OpenSpec layout | one umbrella change, tasks by milestone, one PR per milestone, archive after M7 | pending |
| C19 | Headline metric | account return on USD equity; underlying return and costs split alongside | pending |
| C20 | Annualisation | 365 periods/year for crypto runs, recorded in the snapshot; 252 elsewhere unchanged | pending |
