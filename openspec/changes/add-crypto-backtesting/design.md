# Design: spot crypto backtesting

Values marked **UNVERIFIED** could not be confirmed from a public primary source on
2026-11-24, or public sources disagree. Each one is flagged in `coin_specs.yaml` with a
`source` and an `as_of`, as the FX and futures specs are. Allay confirms them (Kraken account
fee tier, discounted-pair membership, pair minimums) before the milestone that first *charges*
them (M3) merges.

**Venue decision (Allay, 2026-11-24): Kraken.** Crypto trades on Kraken Pro, and backtests
charge the Kraken Pro spot schedule published at <https://www.kraken.com/ca/pro/fees>
(read 2026-11-24). This replaces the first draft's IBKR/PAXOS default. The IB findings in the
survey stay as background only.

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
| IBKR (MCP `search_contracts`) | `BTC` sec type `CRYPTO`, exchange `PAXOS`, conId 479624278; `SOL` `CRYPTO`/`PAXOS`, conId 766137558 | background only: IB is not the crypto venue (decision C2) |
| Kraken Pro spot fees (CA page, read 2026-11-24) | 17 tiers by 30-day spot volume, futures volume or assets on platform (USD); tier 1 0.40% maker / 0.80% taker down to tier 17 0.00% / 0.05%; no minimum fee shown; 0.02% off on 634 "selected spot pairs" (list not readable); margin adds opening and 4-hour rollover fees | the fee model (section 4) |
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
files (same layout, same parser) or Kraken's own history (M7 evaluates it). Because the
venue is Kraken and FirstRate's source exchange is unknown, FirstRate prices may differ from
Kraken's by a small cross-venue basis; M7's evaluation says whether a Kraken-sourced catalog
should replace or check it.

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
  price_precision: 4            # data precision (FirstRate 1h/1d max); 1-minute rows rounded
  size_precision: 8
  size_increment: "0.00000001"
  typical_spread_bps: "2"       # UNVERIFIED modelled full bid/ask spread on Kraken
  data: firstrate               # or none (spec-only)
  kraken:
    pair: BTC/USD               # KrakenPairMapper user pair; id BTC/USD.KRAKEN
    rest: XXBTZUSD              # as KrakenPairMapper.to_kraken_rest maps it today
    altname: XBTUSD             # UNVERIFIED (AssetPairs altname); raw_symbol
    min_order_base: "0.00005"   # UNVERIFIED (AssetPairs ordermin)
    min_order_usd: "0.5"        # UNVERIFIED (AssetPairs costmin)
    tick_size: "0.1"            # UNVERIFIED (AssetPairs tick_size); live rounding only
    discounted_pair: false      # UNVERIFIED: on the 0.02% "selected spot pairs" list?
fee_schedule:                   # Kraken Pro spot ("Spot & Futures" tiers), CA page
  source: https://www.kraken.com/ca/pro/fees
  as_of: 2026-11-24
  default_tier: 2               # decision C21 (Allay's tier); --fee-tier overrides per run
  discounted_pair_bps_off: "2"  # 0.02% off maker and taker on selected pairs
  tiers:                        # maker_pct, taker_pct; thresholds in USD (spot 30d | futures 30d | assets)
    1:  {maker: "0.40", taker: "0.80", spot_30d: "0"}
    2:  {maker: "0.30", taker: "0.60", spot_30d: "2500"}
    3:  {maker: "0.22", taker: "0.38", spot_30d: "10000"}
    4:  {maker: "0.20", taker: "0.35", spot_30d: "25000"}
    5:  {maker: "0.15", taker: "0.30", spot_30d: "50000"}
    6:  {maker: "0.12", taker: "0.25", spot_30d: "100000"}
    7:  {maker: "0.10", taker: "0.22", spot_30d: "250000"}
    8:  {maker: "0.08", taker: "0.20", spot_30d: "500000"}
    9:  {maker: "0.06", taker: "0.18", spot_30d: "1000000"}
    10: {maker: "0.04", taker: "0.15", spot_30d: "2500000"}
    11: {maker: "0.02", taker: "0.12", spot_30d: "5000000"}
    12: {maker: "0.00", taker: "0.10", spot_30d: "10000000"}
    13: {maker: "0.00", taker: "0.09", spot_30d: "50000000"}
    14: {maker: "0.00", taker: "0.08", spot_30d: "100000000"}
    15: {maker: "0.00", taker: "0.07", spot_30d: "250000000"}
    16: {maker: "0.00", taker: "0.06", spot_30d: "400000000"}
    17: {maker: "0.00", taker: "0.05", spot_30d: "500000000"}
```

The tier rates are as published (not UNVERIFIED). Only the futures-volume and assets
thresholds are omitted above; the file carries all three columns for reference (re-read from
the same page on 2026-11-24; tiers 1-2 have no assets threshold).

As shipped in M1 the coins sit under a `coins:` map (like `pairs:` in `pair_specs.yaml`) and
every per-coin value carries a `sources` entry (`status`, `source`, `as_of`):
`price_precision` and `size_increment` are VERIFIED; `typical_spread_bps` and the Kraken
`rest`, `altname`, minimums, tick and `discounted_pair` are UNVERIFIED. The fee schedule's
rates carry the page as their source (VERIFIED); its defaults carry their own entries:
`fee_currency` UNVERIFIED, `discounted_pair_bps_off` and `min_fee_usd: 0` VERIFIED from the
page, and `default_tier` (C21) VERIFIED as tier 2 from Allay's Kraken fee screen
(2026-11-24; it was a provisional tier 1 at M1). `rest` is `KrakenPairMapper.to_kraken_rest`'s output
(SOL gives `SOLZUSD`; Kraken's own name may be `SOLUSD`; M7 checks it against `AssetPairs`). The spot
thresholds are informational: a run uses one fixed tier (decision C21), it does not re-tier as
simulated volume accumulates.

Cross-field checks: base != quote; the key is the base; quote must be `USD` (decision C6);
`size_increment` decimals fit `size_precision`; `kraken.pair` equals `base/quote`; tiers are
numbered 1..N with maker <= taker and rates non-increasing by tier; `default_tier` is a tier.
Shipped coins: BTC, ETH, SOL.

### `build_crypto_pair()` (`src/services/crypto/instrument_builder.py`)

A Nautilus 1.220 `CurrencyPair`:

- `instrument_id = BTC/USD.KRAKEN` (decision C2), the id `KrakenPairMapper.to_nautilus_id`
  already produces, so FirstRate-imported and Kraken-fetched runs (M7) name the same
  instrument. `raw_symbol` = the Kraken altname (`XBTUSD`).
- `price_precision` from the bars when given (Nautilus rejects a mismatch), else the spec.
  `price_increment = 10^-precision` (the data grid; the venue tick stays in `info` for live).
- `size_precision = 8`, `size_increment = 1e-8` (spec-driven).
- `margin_init = margin_maint = 1` (100% of notional: unlevered; decision C8).
  `maker_fee = taker_fee = 0` (the venue fee model charges; section 4).
- `info`: `asset_class = "CRYPTO"`, `typical_spread_bps`, the Kraken block (symbols,
  minimums, tick, discounted-pair flag) and the fee schedule's `source`/`as_of`. The tier is a
  run parameter, not an instrument property.

`build_instrument` routes `CRYPTO` (case-insensitive) to it. Equity, futures and FX paths stay
byte for byte as they are. `is_crypto(instrument)` (core, duck-typed like `is_fx_pair`)
is the one predicate every later milestone keys on.

### Catalog row (no migration, decision C11)

`ticker = BTC`, `nautilus_id = BTC/USD.KRAKEN`, `asset_class = CRYPTO`, `exchange = KRAKEN`,
`currency = USD`, `tick_size` = price increment, `multiplier = 1`, `contract_kind = NULL`.

### Engine-acceptance spike (M1 gate for the whole plan)

A forked integration test proves, before M2-M7 are built on it:

1. A venue with `base_currency=USD` accepts `BTC/USD.KRAKEN` with 4-decimal `LAST` bars and a
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

### Spike result (M1, 2026-11-24): account model A

`tests/integration/core/test_crypto_engine_acceptance.py` (forked, Nautilus 1.220) pins:

1. **Acceptance.** A `KRAKEN` `MARGIN` venue with `base_currency=USD` takes `BTC/USD.KRAKEN`
   with 4-decimal `LAST` bars. A 0.128 BTC market order fills at `0.12800000`, and the round
   trip realises USD P&L (`0.128 x (79000.5 - 78000.1234) = 128.05 USD`) straight into the
   single USD balance.
2. **A passes (with the gate), so A is the decision of record (C8).** `MARGIN` +
   `default_leverage=1` + `margin_init=1` + a prototype gate (the shared `MarginGate` with a
   `qty x price` requirement and a no-short rule): the bracket entry fills, the take-profit
   fills at 80,000 and OCO-cancels the stop, P&L 199.99 USD; the over-cash 2 BTC buy is denied
   `MARGIN_EXCEEDS_FREE_BALANCE: 2 BTC/USD.KRAKEN coin(s) need 156000.2468 USD initial margin,
   free=2199.99 USD`, and a naked `SELL` is denied `CRYPTO_SPOT_NO_SHORT`. **Without the gate**
   the same venue fills the USD 156k buy on USD 10k (free balance clamps to 0) and opens the
   short: Nautilus skips risk checks for margin accounts, so the M3 gate is mandatory for
   every crypto venue, not an add-on.
3. **B is rejected.** A single-currency (USD) `CASH` venue refuses a `CurrencyPair` at
   `add_instrument` ("single-currency CASH account"). A multi-currency `CASH` venue checks only
   priced orders: in a bar-only run the cache has no quote or trade tick, so market orders are
   never checked; the USD 156k market buy fills and Nautilus stops the whole backtest
   (`AccountBalanceNegative`), leaving 2 BTC credited and the USD debit unapplied. It also
   fills a `SELL` of a coin it never held (a short). Only the resting limit buy is denied
   (`NOTIONAL_EXCEEDS_FREE_BALANCE`).
4. **Fill size vs bar volume (C15).** Bar volume never caps fill size: every market order
   fills in full. Volume only sets how much fills at the bar price, and the rest fills one
   price increment (0.0001) worse. Volume 2 fills 0.128 at the bar price and 5 BTC as 0.5 + 4.5;
   volume 0 fills 1e-8 at the price and the remainder one tick worse. At 4-decimal prices the
   slip is immaterial, so no volume handling is needed in M2/M6.
5. **Bar volume precision must equal the size precision (8).** An integer-precision volume
   stops the run (`bar.volume.precision=0 did not match ... size_precision=8`). M2 stores
   volume at 8 decimals for every row, including the post-2026-03-21 whole-coin volumes.

Consequences for M3: under `HEDGING` a `SELL` without a position id opens a new short whatever
else is held, so the no-short rule is: a parentless `SELL` passes only when it targets an open
long and does not oversell it (net of other working sells on that position); bracket legs
(with a parent) pass. The prototype `_PrototypeSpotGate` in the spike is the reference.

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
  `{ID}-{step}-{agg}-LAST-EXTERNAL` (trade prices, as Kraken OHLC and trade bars are).
- **Warnings, not drops**: gaps longer than 10 bars, the volume-rounding regime change (first
  bar whose volume and all later volumes are integers), zero-volume bars. Nothing is filled
  forward.
- **CLI**: `import --format firstrate --asset-class crypto --catalog firstrate-crypto <dir>`,
  all five timeframes by default, `--dry-run` showing per-coin ranges. The 6.1M-row BTC
  1-minute file streams in chunks (peak RSS measured in the manual task; target < 1.5 GB).
- **Re-run classifier**: the inherited classifier probes every FirstRate file as New York
  time, which would never match a UTC series' recorded end, so the crypto service probes the
  source as UTC (`compute_source_last_date(..., source_tz=timezone.utc)`) and skips only when the
  end, the recorded count and the Parquet footers agree.

### Import result (M2, 2026-11-24)

All 15 series imported into `/srv/market-data/catalogs/firstrate-crypto` (17,419,616 bars,
924 MB, 3 min 12 s, peak RSS 787 MB including BTC 1-minute). Each bar count equals its source
line count; first and last bars equal the source; each coin's 2026-05-01 daily bar opens at
00:00 UTC and equals its 24 UTC hourly bars aggregated; the 2024-03-10 BTC hourly series has 24
bars. A re-run skips all 15 in 22 s. Warnings as expected: whole-coin volume from 2026-03-21
00:00 UTC in every series, 5-decimal 1-minute rows rounded (BTC 7,534, ETH 5,397), zero-volume
1-minute bars (BTC 1,176, ETH 100, SOL 5) and gaps of more than 10 bars, mostly 2013-2016.

## 3. Account, leverage and shorting (M3)

- **Account** (decision C8, option A unless the spike says otherwise): crypto venues are
  `MARGIN` with `base_currency=USD` and `default_leverage=1`, like FX. P&L, commission and
  balance are USD natively because every shipped pair is USD-quoted: **no rate feeder** is
  needed. `needs_conversion` stays FX-only. A venue mixing crypto with FX or equities is refused
  (the `KRAKEN` venue only lists crypto in a backtest).
- **No leverage**: `CryptoSpotRequirement.required_initial(qty, price) = qty x price` (USD). The
  shared `MarginGate` rules (new exposure, HEDGING closing credit, venue-wide reservations,
  quantity-increase modify) apply unchanged; over-allocation is denied
  `MARGIN_EXCEEDS_FREE_BALANCE` (existing prefix). Commission headroom is not reserved (a fill
  can take the balance a few dollars below zero; reported, not denied; same as FX).
- **No shorting**: an order is denied `CRYPTO_SPOT_NO_SHORT` when the net position after it
  (counting other working orders on the same side) would be negative. Under `HEDGING` that
  means a parentless `SELL` must target an open long by position id and not oversell it (the
  M1 spike, section 1). Reduce-only exits and bracket legs that only close are allowed.
  Futures and FX reasons are unchanged.
- **Results**: `results_extractor`, `backtest_persistence` and `mark_to_market` already accept a
  USD-quoted linear `CurrencyPair`; M3 adds tests that pin fractional quantities through the
  ledger, the `trades` rows and `_check_against_accounts`. `config_snapshot` gains
  `account_currency: "USD"`, `fee_tier` and `crypto_pairs` (per instrument: tier rates used, spread,
  precision, `as_of`), declared on `StrategyConfigSnapshot` and omitted when unset.

## 4. Costs (M3)

### Commission (decision C3: Kraken Pro spot)

`CryptoCommissionModel(FeeModel)` returns USD `Money`:

```
trade_value = fill_qty x fill_px                          # USD-quoted
rate        = tier.maker if fill.liquidity_side == MAKER else tier.taker
rate       -= discounted_pair_bps_off / 1e4 if coin.discounted_pair (floored at 0)
commission  = rate x trade_value x cost_multiplier        # no minimum
```

- **Maker vs taker**: Nautilus marks a fill `MAKER` when it came from an order that rested on
  the book (a limit order filled by a later bar) and `TAKER` for market and stop-market orders
  and for limits that were marketable on arrival. That is how Kraken charges. A limit order
  that fills in the bar it was submitted on is a taker fill, which is conservative. Riptide and
  Bollinger's limit entries and take-profits mostly pay maker; stops and market exits pay taker.
- **Tier (decision C21)**: one tier per run, default `default_tier` (**tier 2**, Allay's
  Kraken Pro tier: 0.30% maker / 0.60% taker), `--fee-tier N` to override, recorded in
  `config_snapshot.fee_tier`.
  Volume-driven re-tiering inside a run is not modelled.
- **No minimum fee**: the page shows none. Charges are rounded to cents once per order from
  running totals (the FX model's mechanism, shared), so split bar fills do not add rounding.
- **Fee currency**: the page does not say which currency Kraken charges fees in. The model
  charges USD (the quote), which gives the same USD P&L whichever currency Kraken uses.
- **Examples**: USD 9,984 of BTC (0.128 at 78,000) costs, at the default tier 2, USD 59.90
  taker or USD 29.95 maker; at tier 1, USD 79.87 taker or USD 39.94 maker; at tier 4, USD 34.94
  taker. A taker round trip at tier 2 costs 1.2% of notional before spread (1.6% at tier 1),
  which is enough to sink many intraday strategies. Reports show it (section 10).
- `KRAKEN_DEFAULT_MAKER_FEE`/`TAKER_FEE` (0.16%/0.26%) are outdated against this schedule.
  M7 makes the spec the source and keeps the settings only as an explicit override.

Source: Kraken Pro fees, Canada page (<https://www.kraken.com/ca/pro/fees>, read
2026-11-24), "Spot & Futures" tier table. The same page's stablecoin/FX table
(0.20%/0.20% at tier 1) applies to pairs like USDT/USD, which this change does not trade. Its
futures/perps table is kept for the perps change: tiers 11-17 differ between two lists on the
page, and an RWA-perps promotion runs from 2026-11-12 to 2027-02-10.

### Spread and slippage (decision C4)

Single trade-price series, so the spread is **modelled**, exactly as FX does: each fill is
charged `qty x price x typical_spread_bps / 2 / 1e4` (a half-spread per side) as a separate
component of the same fee model, scaled by cost stress, overridable with `--spread-bps` or
`CRYPTO_SPREAD_BPS_<COIN>`. Defaults (**UNVERIFIED**, full spread): BTC 2 bp, ETH 3 bp,
SOL 5 bp, on Kraken's USD books. Both components are recorded separately in `config_snapshot.crypto_pairs[...].costs_usd`
and in reports.

### M3 result (2026-11-24)

Implemented as above (`src/core/crypto_costs.py`, `src/core/margin_gate.py`,
`src/core/backtest_venue.py`), with these details settled in code:

- **Denial text**: the spot requirement is quantized to cents like FX:
  `MARGIN_EXCEEDS_FREE_BALANCE: 2 BTC/USD.KRAKEN coin(s) need 156000.00 USD initial margin,
  free=2152.42 USD` (the spike's prototype printed unrounded `156000.2468`). The no-short reason
  is `CRYPTO_SPOT_NO_SHORT: SELL <qty> <id> would go short; a spot sell must close an open long
  by position id (free to sell: <n>)`. Reduce-only sells are checked too, and every working sell
  on the long claims it (bracket siblings, which share a parent, once): otherwise a reduce-only
  close could flatten the long under a resting limit sell that later fills as a short
  (CodeRabbit, PR #72). A strategy cancels its resting sells before closing at market. A crypto
  `SELL` only ever closes, so it holds no in-flight margin and its reprices are not margined.
- **Rounding**: maker and taker are kept as separate per-order running totals (an order can
  fill partly as taker on arrival and rest for the rest), each rounded to cents once; cost
  stress multiplies before rounding.
- **Tier**: carried on the pair as `info["fee_tier"]` (absent when the default applies), so
  neither the orchestrator nor the venue signature grows; `--fee-tier` (task 3.10, moved from
  M5) rebuilds the pair at the tier, as `--spread-pips` does for FX.
- **Metrics**: 365 applies to the mark-to-market metrics (the headline). When mark-to-market is
  unavailable the realised fallback stays Nautilus' 252-day statistic, as section 9 says.
- **Smoke** on the real catalog: `docs/agent/crypto-backtesting.md` ("Account, costs and the
  spot gate").

## 5. Position sizing (M4)

Units are **base coin units**, fractional (BTC qty 0.128 = 0.128 BTC).

Framework-free helpers in `src/core/position_sizing.py` (USD quote only, so no rate argument):

- `crypto_units_for_notional(notional_usd, price, size_increment)` =
  `floor_to_increment(notional / price)`.
- `crypto_units_for_risk(risk_usd, stop_distance, size_increment)` =
  `floor_to_increment(risk / stop_distance)`.
- **No minimum of 1**; a result below Kraken's minimum (`min_order_base` or `min_order_usd`)
  becomes 0 (no order) and is counted once per run in the run warnings (decision C13).
- Quantities are passed to Nautilus as `instrument.make_qty(units)`, never `Quantity.from_int`.

`sma_crossover` and `buy_and_hold` route crypto through these. `backtest run --units X` for
crypto accepts a decimal > 0 that is a multiple of the size increment (FX keeps int >= 1;
futures `--contracts` unchanged); MCP `submit_backtest`/`validate_config` follow the same rules.

**As built (M4, 2026-11-24).** `crypto_units(notional, price, pair)` applies the increment and
both Kraken minimums of a spec-built pair. A fixed `--units` size must also be at or above the
coin's `min_order_base` (a fixed size under it would drop every order, so it is refused up front,
exit 2); the USD minimum is not checked there (no price yet), and is far below the base one for
BTC/ETH/SOL. "Counted once per run" is a strategy log: `sma_crossover` warns on the first drop
and logs the run's drop count on stop. NTrader has no persisted run-warnings channel yet, so
showing the count in `show`/MCP/dashboard is left to M5 reporting. The MCP `units` argument
takes a JSON integer or number; FX still requires an integer.

## 6. Ticks and fills

The price increment is the data grid (10^-4). Kraken's `tick_size` (UNVERIFIED) are stored for live. `GapAwareFillModel` is unchanged; with 24/7 data,
gaps are rare (exchange outages, sparse early years). Fill size against bar volume is pinned by
the spike (section 1, spike result item 4): orders always fill in full, the part beyond the
bar's volume one increment worse; the importer does not inflate volume.

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

### M6 result (2026-11-24): exit timing (decision C24)

Strategies PR #6 found that the M3 gate counted a reduce-only hard stop **pending cancel**
against the long, so Riptide's green/time exit (cancel the stop, send a reduce-only market
close beside it) was denied `CRYPTO_SPOT_NO_SHORT` and the long never closed. #6 worked around
it in the strategy by sending the close once the cancel landed, which fills crypto exits one
bar later than equities under next-bar execution (`gap_aware`).

Options considered:

1. **Keep the strategy workaround**: correct but one bar late, and every future strategy that
   cancels a stop and closes in the same bar has to repeat it.
2. **Count a pending-cancel order as released for every sell**: on time, but a plain sell
   beside the cancel could go short if the cancel failed (the stop fills first, then the sell).
3. **Release only reduce-only for reduce-only** (chosen, Allay 2026-11-24): a reduce-only
   `SELL` being submitted does not count reduce-only sells that are `PENDING_CANCEL` on the
   same long. Everything else still claims the long: a non-reduce-only order pending cancel,
   working stops and limits, and a close already in flight (so a second close is denied).
   Nothing is released while a plain (not reduce-only) sell works on the long or is in the
   same submission: the venue clamps only reduce-only fills, so if the cancel failed the
   reduce-only sells could flatten the long first and the plain sell would fill as a short.
   Safe if the cancel fails: Nautilus' matching engine (`use_reduce_only`, on by default)
   fills a reduce-only order only up to the position's size and cancels resting reduce-only
   orders once the position is flat, so the pair cannot go short.

Implemented in `src/core/margin_gate.py` (`_spot_free_to_sell` returns the free size and the
free size for a reduce-only sell); pinned in `tests/component/core/test_margin_gate_crypto.py`
(`TestExitBesideAStopPendingCancel`) and `tests/integration/core/test_crypto_venue_engine.py`
(a real engine: the exit fills on the signal bar; a second close, an oversell and a naked sell
beside the cancel are denied). Strategies PR #7 (merge `67ebddb`, pinned) removes the
workaround; with it every Riptide
crypto exit in the smoke runs fills one bar after its signal, as on equities
(`docs/agent/crypto-backtesting.md`, M6). Bollinger's crypto config defaults to 1-day bars
(decision C25).

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
| run: costs split | commission (maker vs taker) vs modelled spread, USD; fee tier |

**Headline (decision C19)**: account return on USD equity, Sharpe/drawdown annualised on 365.
Return on the underlying and costs alongside. Every crypto report states "spot, unlevered,
long-only; Kraken Pro fee tier N; spread modelled".

- CLI `show`/`report`/`history`: a crypto block (`BTC/USD (crypto, KRAKEN, fee tier 2)`), fractional
  quantities. Equity, futures and FX output byte-identical.
- MCP `get_run`/`compare_runs`/`export_results`: `asset_class: "crypto"`, a `crypto` object,
  crypto trade columns only when a crypto run is exported.
- `backtest run`: `--symbol BTC|BTCUSD|BTC/USD` resolves through the catalog row to
  `BTC/USD.KRAKEN`; `--timeframe 1-HOUR` maps to `1-HOUR-LAST` (`price_type_for(CRYPTO)` =
  `LAST`); `--units X` decimal; `--fee-tier N` (1-17, default from the spec, decision C21); `--spread-bps`.
- **Dashboard (needs a FROZEN_VIEWS ruling, decision C12)**: the FX ruling's file set with a
  crypto branch on each: `src/api/models/backtest_detail.py` (`view.crypto`), new
  `templates/partials/crypto_panel.html`, `templates/backtests/detail.html` (include),
  `src/api/ui/backtests.py` (Run form bar type and decimal units),
  `templates/partials/trades_table.html` (Quantity with decimals, Notional $, Underlying %,
  P&L $), `src/api/rest/trades.py` (export), `src/api/models/backtest_list.py` and
  `templates/backtests/list_fragment.html` (`CRYPTO` badge), `src/cli/commands/compare.py`.
  Equity, futures and FX pages stay byte-identical against goldens captured before the change.

### M5 result (2026-11-24)

- **Figures and words**: `src/services/crypto/returns.py` (pure) and `display.py` feed every
  renderer. Coin quantities print at the pair's size precision with trailing zeros trimmed
  (`0.12820512`, `0.25`), never rounded to whole units or 2 dp. Peak exposure reuses the FX
  peak walk (`src/services/fx/returns.peaks`; unlevered, so margin = notional). Every crypto
  report shows `BTC/USD (crypto, KRAKEN, fee tier N)`, maker vs taker commission, modelled
  spread, 365-day annualisation, USD, and the note "Spot, unlevered, long-only; Kraken Pro fee
  tier N; spread modelled".
- **Below-minimum drop count (decision M5-D1)**: persisted in the run's existing JSONB
  `config_snapshot`, as `crypto_pairs[<id>].orders_below_minimum` (an int), beside the per-pair `costs_usd` M3 already stores there. **No DB migration**: the
  snapshot is where every other per-run crypto term lives (decision C11), and the key is
  inside the already free-form `crypto_pairs` dict, so `StrategyConfigSnapshot` and equity,
  futures and FX rows are unchanged. Mechanism: a strategy that counts drops starts the pair's
  count at 0 on start (`track_orders_below_minimum`) and adds each drop
  (`count_order_below_minimum`) in the run's Nautilus cache (key
  `ntrader.crypto_orders_below_minimum`, the same pattern as the cost totals);
  `crypto_snapshot` writes it when the run is saved. A pair no strategy counted, and every run
  saved before M5, has no key and reports "not recorded" (`null` in MCP/JSON), never a false
  0. `sma_crossover` and `buy_and_hold` count; the submodule strategies (Riptide, Bollinger)
  should in M6.
- **Peak exposure caveat (PR review)**: a trade row stores a position's largest quantity and
  its open time, not its fill history, so peak notional/exposure count a position that scales
  in at its largest size from its open, and the shared peak walk counts a position opened and
  closed at one timestamp as held then. Exact for single-entry positions that span bars (the
  core strategies' usual case); an upper bound otherwise, and the report note says so. An exact figure needs a
  persisted per-position quantity series (a persistence change, not M5).
- **Symbol forms**: `src/services/crypto/symbols.py` derives the aliases from
  `coin_specs.yaml` (coin, Kraken `pair`, `altname`, `rest`), so `BTC`, `BTCUSD`, `BTC/USD`,
  `BTC-USD`, `XBTUSD`, `XBT/USD`, `XXBTZUSD` and `BTC/USD.KRAKEN` are all `BTC` in `backtest
  run` (named catalogs; `--units`, `--fee-tier` and `--spread-bps` follow), the MCP
  `submit_backtest`/`validate_config` request and the Run form. A crypto-shaped symbol no spec
  ships is refused naming the shipped pairs: another quote (`BTC/EUR`, `BTCUSDT`), another
  venue (`BTC/USD.BINANCE`), an unknown coin (`DOGE/USD`, `DOGEUSD.KRAKEN`) or an alias two
  coins share (ambiguous); CLI exit 2, MCP `unknown_crypto_pair`. Equity tickers that merely
  start with a coin (`ETHE`, `ETHA`, `BTCO`) and FX pairs are not crypto and pass through.
  `--data-source kraken` keeps its own `BTC/USD` form (M7 owns that path).
- **`--spread-bps X`**: replaces the coin's modelled full spread for the run
  (`BacktestRequest.crypto_spread_bps`, in the config hash only when set), recorded as
  `crypto_pairs[<id>].typical_spread_bps`.
- **Dashboard (C12, Allay's sign-off 2026-11-24)**: changed exactly `backtest_detail.py`,
  `backtest_list.py`, `rest/trades.py`, `detail.html`, `list_fragment.html`,
  `trades_table.html`, `compare.py` and the new `crypto_panel.html`; `ui/backtests.py` needed no
  change (the trades-table cells come from `fx/display.trade_table_cells`, the Run form's
  symbol from `fx/symbols.run_target`; the form has no units field, so decimal units stay a
  CLI/MCP option). FX goldens were captured before the edits
  (`tests/fixtures/fx_dashboard_golden/`); equity, futures and FX pages and the CLI/MCP output
  of an existing equity, futures and FX run are byte-identical before and after.

## 11. Kraken data path correction (M7, decision C16)

- `to_kraken_charts` returns the perpetual symbol `PF_XBTUSD`. The Kraken Charts API may also
  serve spot series (**UNVERIFIED**: tick type and symbol format to be confirmed against the
  live API in M7's first task); Kraken's spot REST `OHLC` endpoint returns only the most recent
  720 candles per interval. M7 switches to the spot series if the Charts API has it; otherwise
  `--data-source kraken` refuses a range older than the REST window with a clear message.
  It never again wraps perpetual prices in a spot instrument.
- The fetched instrument is built by `build_crypto_pair(get_coin_spec(base))` when the coin has a
  spec (precision from the bars), else the current fallback with the same fee schedule, and the
  venue gets `CryptoCommissionModel` at the run's tier.
- **Same-venue history (UNVERIFIED)**: Kraken publishes downloadable historical OHLCVT/trade
  files. M7's first task checks coverage and format for BTC/ETH/SOL and recommends whether a
  `kraken-crypto` catalog should replace FirstRate as the primary source or serve as a basis
  check. Importing it would be a follow-up (it is a different file layout).
- `KRAKEN_DEFAULT_MAKER_FEE`/`TAKER_FEE` remain as overrides; their defaults move to the spec.
- Code moves out of `kraken_client.py` and `_backtest_helpers.py` so neither grows.

## 12. Live-trading compatibility notes (not implemented here)

- **Adapter**: Nautilus 1.220, the pinned version, has no Kraken adapter. Live Kraken trading
  needs either a newer Nautilus release that ships one (**UNVERIFIED**; check the changelog
  before the live effort) or a custom adapter. Upgrading Nautilus is a repo-wide change with
  its own risk.
- **Ids**: `BTC/USD.KRAKEN` is the id NTrader's Kraken path already uses; a live adapter's
  symbology must be mapped to it (or the catalog rows renamed) so backtest configs run live
  unchanged.
- **Orders**: round to Kraken `tick_size` and `lot_decimals`, respect `ordermin`/`costmin`
  (section 6, UNVERIFIED values). Check that Kraken spot accepts the strategies' order kinds
  (stop-loss, take-profit, OCO brackets) before going live; Kraken's conditional-order model is
  not Nautilus' OCO.
- **Fees live**: Kraken reports the actual fee per fill, so the live trade recorder needs no
  model; the backtest tier should match the account's tier for paper/live comparisons.
- **Account**: spot without margin is long-only and cash-funded. Kraken margin (opening plus
  4-hourly rollover fees) would need its own model.
- **Sessions**: 24/7; no regular-trading-hours flag applies.
- **Guard lists**: any live crypto change touches `src/core/live_*` and the
  `NODE_FACING_MODULES`/`EMITTED_*` lists (`docs/agent/guard-lists.md`).
- **IB background**: the IB adapter builds PAXOS crypto as an inverse `CryptoPerpetual`
  (P&L in BTC). Irrelevant while Kraken is the venue, but a trap if IB crypto is ever added.

## 13. Decisions needed from Allay

C2 and C3 were decided by Allay on 2026-11-24 (Kraken). On the same day Allay accepted the
recommendations for C1, C5-C8, C13, C14, C17 and C20; C8 was then settled by the M1 spike
(section 1: account model A). C21-C23 were **provisional defaults** recorded at M1; Allay
confirmed C21 (tier 2) on 2026-11-24, and C22-C23 remain pending his confirmation. The other rows remain recommendations. UNVERIFIED values are proposed
working defaults; M3 does not merge until the fee-related ones are confirmed or accepted as
defaults.

| # | Decision | Recommendation | Status |
|---|---|---|---|
| C1 | Spot only, or also perps/futures | **Spot only** (`CurrencyPair`); perps (`CryptoPerpetual` + funding) as a separate later change; CME MBT/MET via the futures path if wanted | **accepted by Allay, 2026-11-24** |
| C2 | Venue / instrument id | **Kraken**: `BTC/USD.KRAKEN` (the id the existing Kraken path uses); catalog `ticker = BTC` | **decided by Allay, 2026-11-24** |
| C3 | Fee model | **Kraken Pro spot schedule** (CA page, 17 tiers, tier 1 0.40% maker / 0.80% taker, no minimum), maker/taker by fill liquidity side | **decided by Allay, 2026-11-24** |
| C4 | Spread | modelled half-spread per side: BTC 2 / ETH 3 / SOL 5 bp full spread (UNVERIFIED), `--spread-bps` override | pending |
| C5 | Data source, symbols, bar sizes | **FirstRate BTC, ETH, SOL, all five timeframes** into `firstrate-crypto`; smoke and strategy configs on 1-day and 1-hour; source venue UNVERIFIED (ask FirstRate if it matters) | **accepted by Allay, 2026-11-24** |
| C6 | Quote currency | **USD only** (the FirstRate data is USD; Kraken lists BTC/USD, ETH/USD, SOL/USD); CAD, USDT/USDC and coin-quoted pairs out of scope | **accepted by Allay, 2026-11-24** (see C22) |
| C7 | P&L / reporting currency | **USD** account; no conversion needed for USD quotes; non-USD quotes refused at spec load | **accepted by Allay, 2026-11-24** |
| C8 | Leverage / margin for spot | **Unlevered, long-only**: `MARGIN` + `base_currency=USD` + `margin_init=1` + gate (`CRYPTO_SPOT_NO_SHORT`); fall back to a Nautilus `CASH` account only if the M1 spike rejects this | **accepted by Allay, 2026-11-24; M1 spike result: A passes (with the gate), B rejected** (section 1) |
| C9 | Funding rates | not applicable to spot; the perps change must source Kraken/other funding history first | pending |
| C10 | Day / daily bar timestamp | UTC: daily bar at `D 00:00 UTC` (bar open), trade date = UTC date, equity cut at UTC midnight | pending |
| C11 | Schema | **no migration**: fractional quantities fit `Numeric(20, 8)`; terms in `config_snapshot.crypto_pairs` | pending |
| C12 | Dashboard | pre-approve a FROZEN_VIEWS regeneration for exactly the section 10 files (FX ruling set plus `crypto_panel.html`) | **signed off by Allay for M5, 2026-11-24** (M5 result, section 10) |
| C13 | Sizing unit | fractional coin units floored to `size_increment`, no minimum of 1, below-minimum orders dropped and counted; decimal `--units` | **accepted by Allay, 2026-11-24** |
| C14 | Price precision | fixed 4 decimals per coin (the 1h/1d data maximum); 1-minute 5-decimal rows rounded half-even and counted | **accepted by Allay, 2026-11-24** |
| C15 | Volume | store as-is (coin units, 8 dp); warn on the 2026-03-21 whole-coin rounding; no volume inflation; spike pins fill behaviour | pending |
| C16 | Kraken path | fix to spot series or refuse old ranges (M7); never perp prices in a spot instrument | pending |
| C17 | Coin universe | BTC, ETH, SOL (the data on the VPS) | **accepted by Allay, 2026-11-24** |
| C18 | OpenSpec layout | one umbrella change, tasks by milestone, one PR per milestone, archive after M7 | pending |
| C19 | Headline metric | account return on USD equity; underlying return and costs split alongside | pending |
| C20 | Annualisation | 365 periods/year for crypto runs, recorded in the snapshot; 252 elsewhere unchanged | **accepted by Allay, 2026-11-24** |
| C21 | Fee tier | one fixed tier per run, **default tier 2** (Allay's Kraken Pro spot tier: 0.30% maker / 0.60% taker); `--fee-tier N` overrides; no re-tiering inside a run | **confirmed by Allay, 2026-11-24** (source: Allay's Kraken fee screen; `coin_specs.yaml` `default_tier: 2`, VERIFIED) |
| C22 | Trading currency on Kraken | **USD pairs**, to match the data. If Allay will trade BTC/CAD etc., the account and reports need CAD (FX conversion via the FX rate machinery), which is a scope change | provisional default (USD pairs), pending Allay's confirmation: which currency will you trade in? |
| C23 | Discounted pairs | assume BTC/ETH/SOL are **not** on the 0.02%-off list (conservative) until confirmed | provisional default (`discounted_pair: false` per coin), pending Allay's confirmation |
| C24 | Exit beside a stop's cancel | the gate lets a reduce-only close take the claim of a reduce-only sell pending cancel, so exits fill on the same bar as equities (section 8, M6 result); the strategy-side wait in strategies #6 is removed by #7 (pinned `67ebddb`) | **decided by Allay, 2026-11-24** |
| C25 | Bollinger crypto bar size | `configs/bollinger_crypto.yaml` defaults to 1-day bars | **decided by Allay, 2026-11-24** |
