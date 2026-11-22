# Design: spot FX backtesting

Values marked **UNVERIFIED** are best-known public figures. Allay must confirm them against
the IBKR account (Client Portal, contract details, margin requirements) before M4 merges.
Each one is also flagged in `pair_specs.yaml` with a `source` and an `as_of`, as the
futures specs were.

## 0. Survey (2026-11-22)

### What exists today

| Area | State | FX impact |
|---|---|---|
| `AssetClass` (`src/models/catalog.py`) | `FX` member exists; `import_data.py` maps `--asset-class fx` | no parser registered, so it exits 2 |
| Instruments | `build_instrument` (`firstrate/backtest_loader.py`): `FUTURES` goes to `build_futures_contract`, everything else to `build_equity` | FX would become an `Equity` |
| `CurrencyPair` | Built for Kraken crypto (`kraken_client.build_currency_pair`, `_backtest_helpers` fallback), ids `BTC/USD.KRAKEN` | precedent for the `/` symbol and the catalog path |
| Futures spec registry | `src/services/futures/contract_specs.yaml` with `SpecAssetClass` incl. `"FX"` (for currency futures, unused) | template for `pair_specs.yaml` |
| IB contract model | `ibkr_venue_provider.sec_type: Literal["STK","CFD","CMDTY","IND"]`; `ibkr_data_provider` hardcodes `secType="STK"`, `1-DAY-LAST` | no `CASH` anywhere; IB FX download is a follow-up |
| Nautilus IB adapter 1.220 | `VENUES_CASH = ["IDEALPRO"]`; `RE_CASH` parses `EUR/USD`; `parse_forex_contract` returns a `CurrencyPair` (margin and fees 0, precision from `minTick`) | id scheme `EUR/USD.IDEALPRO` is live-native |
| IBKR (MCP search) | `EUR`, sec type `CASH`, exchange `IDEALPRO`, conId 12087792 | confirms the venue naming |
| Catalog layout | `/srv/market-data/catalogs/<name>/data/bar/<ID>-<spec>-LAST-EXTERNAL/`; instruments are rebuilt from `catalog_instruments` rows, not stored | FX adds `firstrate-fx`, `-MID-EXTERNAL` |
| Bar price type | CLI hardcodes `f"{timeframe}-LAST"` (`_backtest_helpers.py:295`), import maps (`import_data.py:45`), `dry_run.py`, `reproduce.py` | FX needs `MID` (D2) |
| Venue | `add_backtest_venue`: `MARGIN`, `HEDGING`, `starting_balances=[Money(x, USD)]`, **no `base_currency`**, so the account is multi-currency | JPY P&L would sit in a JPY balance |
| Cost models | `IBKRCommissionModel` per share (+ `slippage_per_share`); `FuturesCommissionModel` per contract; `venue_cost_models` picks by type | needs `FxCommissionModel` |
| Margin | `FuturesMarginModel` (dollars/contract) plus `FuturesMarginGate` on `RiskEngine.execute`; others use Nautilus `LeveragedMarginModel` | FX margin is % of notional (native), but nothing enforces it |
| Sizing | `src/core/position_sizing.py` (`units_for_notional`, `whole_contracts`, `instrument_multiplier`), submodule `_sizing.py` (`notional_quantity`, `risk_quantity`) | multiplier 1, so FX takes the equity path: wrong for non-USD quotes, min-1-unit rule |
| Results | `results_extractor._fills` raises on non-USD commission; `mark_to_market` requires USD-quoted linear instruments; `_check_against_accounts` reads `balance_total(USD)`; `backtest_persistence` drops the currency from `"x JPY"` | the USD-only assumptions to lift (M3) |
| Reporting | `src/services/futures/returns.py` and `display.py`, branch on `config_snapshot.futures_contracts`; dashboard futures panel (FROZEN_VIEWS ruling 2026-11-22) | FX gets a sibling `src/services/fx/returns.py` |
| CLI | `backtest run --contracts N` (futures only), `--catalog`, `--symbol`, `--timeframe` | `--units N` for FX |
| Strategies | `riptide` (notional sizing, cents rounding, `$` ADV filter, long-only), `bollinger_reversal` (risk sizing, `Price.from_str(.2f)` brackets, ISO-week from UTC `ts_event`) | see section 10 |
| Live | `live_node_builder` uses default IB symbology and `use_regular_trading_hours`; `live_trade_recorder` emits `trade.commission_mixed_currency`; `LiveAccountView.from_session` refuses non-USD P&L | notes only (section 12) |

### FX data on the VPS (read-only)

Raw: `/srv/market-data/raw/fx/` (in `MANIFEST.sha256`). There is **no FX catalog**:
`catalogs/` holds only `firstrate-futures`, `firstrate-etf` and `firstrate-stocks`.

| Pair | Timeframes | Range (ET) | Rows (1min / 5min / 30min / 1h / 1d) |
|---|---|---|---|
| EURUSD | 1min, 5min, 30min, 1hour, 1day | 2010-01-03 17:00 to 2026-04-30 23:59 (daily to 2026-05-01) | 6,050,178 / 1,215,629 / 202,793 / 101,471 / 4,241 |
| GBPUSD | same | same | 6,042,317 / 1,214,776 / 202,730 / 101,452 / 4,239 |
| USDJPY | same | same | 6,044,472 / 1,214,696 / 202,756 / 101,468 / 4,240 |

Files are about 325 MB per pair at 1 minute and 1.2 GB in total.

- **Format**: headerless CSV. Intraday rows are `YYYYMMDD,HH:MM:SS,O,H,L,C,V` (7 fields,
  date and time in **separate columns** with a compact date, unlike the futures/equity
  `YYYY-MM-DD HH:MM:SS` single column). Daily rows are `YYYYMMDD,O,H,L,C,V` (6 fields, no
  open interest).
- **Timezone**: US/Eastern wall clock with DST. Every week opens at **Sunday 17:00 ET** in
  both winter and summer (about 207 Sundays each), and the last hourly bar is **Friday
  16:00** (closing 17:00 ET), so the clock follows New York DST, not a fixed offset. Bars
  are stamped at the **bar open**. This matches FirstRate's FAQ ("US Eastern ... except
  crypto").
- **Daily bars use the 17:00 New York cutoff.** I rebuilt daily bars from the hourly bars
  with `trade_date = (ts_ET + 7h).date()`. 299 of the last 300 daily bars matched
  (open/high/close). With a midnight cutoff, 45-50 of 300 matched. A daily row dated `D`
  covers `D-1 17:00` to `D 17:00` ET, and Monday's bar opens Sunday 17:00. The final daily
  row (2026-05-01) is partial and extends past the intraday files.
- **Prices**: trailing zeros are stripped (`1.43`, `1.43008`). The widest significant
  precision is **5 for EURUSD/GBPUSD and 3 for USDJPY**. USDJPY has **float-noise rows**
  in 2014 only (`108.33599`, `107.96999`): 16 hourly rows and 215 at 5 minutes. They round
  cleanly to 3 decimals. No row in any file breaks the OHLC rules (high >= low, open and
  close within range).
- **Volume**: positive on every 5min/30min/1h/1d bar. There are 1,471 / 2,333 / 1,472
  zero-volume 1-minute bars (EURUSD / GBPUSD / USDJPY). This is almost certainly **tick
  count**, not traded size (OTC FX has no tape). **UNVERIFIED** (FirstRate does not
  document it).
- **Bid, ask or mid**: **UNVERIFIED**. FirstRate's FX page does not say whether bars are
  built from bid, ask or mid. There is one series per pair, so no spread can be derived.
- **Anomalies**: one Saturday hourly bar (EURUSD/GBPUSD 2023-08-05 19:00 ET), a few
  Sundays opening at 02:00, 16:00, 18:00 or 22:00 instead of 17:00, a few early Friday
  closes (holidays), and two Sunday-dated daily rows in EURUSD (one in USDJPY).

Source proposal: these three pairs are enough for M1-M6. More pairs or real crosses can come
from (a) more FirstRate FX files (same layout, same parser) or (b) IB historical
`MIDPOINT`/`BID`/`ASK` bars once `ibkr_data_provider` learns `CASH` (follow-up). Generic
CSV through `csv_loader` is possible but has no session/precision rules, so it is not
recommended.

## 1. Instrument model (M1)

### `src/services/fx/pair_specs.yaml`

Same shape and validation discipline as `contract_specs.yaml`: `schema_version`,
`specs_as_of`, pydantic with `extra="forbid"`, and `FxSpecError` naming the file and the dotted
path. Per pair (key = FirstRate ticker, e.g. `EURUSD`):

```yaml
EURUSD:
  base: EUR
  quote: USD
  venue: IDEALPRO            # as IB shows it
  pip_size: "0.0001"         # reporting unit; JPY pairs 0.01
  price_precision: 5         # data precision (FirstRate); = IB minTick decimals
  min_tick: "0.00005"        # UNVERIFIED (IB IDEALPRO half-pip)
  size_increment: "1"        # UNVERIFIED (IB sizeIncrement for CASH)
  min_order_usd: "25000"     # UNVERIFIED: IDEALPRO minimum; smaller orders are odd lots
  typical_spread_pips: "0.5" # UNVERIFIED modelled all-in spread
  margin_pct: "3.0"          # UNVERIFIED IB initial margin % of notional
  maint_margin_pct: "3.0"    # UNVERIFIED
  margin_as_of: 2026-11-22
  session: {open: "17:00", close: "17:00", tz: America/New_York, days: Sun-Fri}
  ib: {sec_type: CASH, symbol: EUR, currency: USD, exchange: IDEALPRO, local_symbol: EUR.USD}
commission:                  # one IB FX schedule, not per pair
  rate_bps: "0.20"           # UNVERIFIED for the account's tier
  min_per_order_usd: "2.00"  # UNVERIFIED
  as_of: 2026-11-22
```

Cross-field checks: the `pip_size` and `min_tick` decimals fit `price_precision`; `base != quote`;
the key is `base + quote`; the `ib` block agrees with base/quote/venue; maintenance <= initial.
Shipped pairs: EURUSD, GBPUSD and USDJPY, plus EURGBP as a data-less cross so the
conversion path has a real spec (marked `data: none`).

### `build_fx_pair()` (`src/services/fx/instrument_builder.py`)

A Nautilus 1.220 `CurrencyPair`:

- `instrument_id = InstrumentId(Symbol("EUR/USD"), Venue("IDEALPRO"))`, i.e.
  `EUR/USD.IDEALPRO`. This is exactly what the IB adapter's IB_SIMPLIFIED symbology produces
  and parses (`RE_CASH`), so a backtest bar type and a live subscription name the same
  instrument (D1). `raw_symbol = EUR.USD` (IB `localSymbol`).
- `base_currency`/`quote_currency` from the spec. `price_precision` follows the bars when
  given (Nautilus rejects a mismatch, same rule as futures), otherwise the spec.
  `price_increment = 10^-precision`, the data grid, not the IB min tick. The tick stays in
  `info` for live (see section 7).
- `size_precision = 0`, `size_increment = 1` (whole base units; spec-driven).
- `margin_init`/`margin_maint` = spec % / 100 (Nautilus' native fraction-of-notional model
  fits FX, unlike futures). `maker_fee = taker_fee = 0` (commission comes from the fee model).
- `info`: `pip_size`, `min_tick`, `min_order_usd`, `typical_spread_pips`, margin
  `as_of`, commission terms, `ib` block, `asset_class="FX"`.

`build_instrument` routes `FX` (case-insensitive) to `build_fx_pair(get_pair_spec(ticker))`.
Every other value is unchanged. The futures and equity paths stay byte for byte as they are.

### Catalog row (no migration, D11)

`ticker = EURUSD` (FirstRate filename), `nautilus_id = EUR/USD.IDEALPRO`, `asset_class = FX`,
`exchange = IDEALPRO`, `currency` = quote, `tick_size` = `min_tick`, `multiplier = 1`,
`contract_kind = NULL`, `roll_rule = NULL`. Base and quote are recoverable from the spec and
the id, so no new columns are needed.

### Engine-acceptance spike (M1 gate for the whole plan)

A forked integration test proves, before M2-M6 are built on it, that:

1. A `MARGIN` venue with `base_currency=USD` accepts the three `CurrencyPair`s and their
   `MID` bars.
2. A USDJPY round trip updates the USD balance by `pnl_jpy / USDJPY` when an exchange rate
   is available. Without a rate, Nautilus logs "Cannot calculate account state" and
   **leaves the balance unchanged** (`accounting/manager.pyx`
   `_update_balance_single_currency` returns False). The test pins that failure mode so
   M3's guard has something to catch.
3. The rate mechanism of section 3 provides the rate.

If (3) fails, D4's fallback applies (section 3) and M3 is re-planned before M2 merges.

## 2. Importer (M2)

`FirstRateFxParser` (`src/services/firstrate/parsers/firstrate_fx_parser.py`), registered for
`AssetClass.FX`, and `FxImportService` (`src/services/firstrate/fx_import_service.py`), a
subclass of `ImportService`. Both are cloned in structure from the futures pair, sharing
helpers where the futures code already factored them (chunked read, footer verification,
`.import-incomplete` marker, classifier).

- **Layout**: `pd.read_csv(names=[d, t, O, H, L, C, V], chunksize=250_000)`. A 6-field row
  is daily, a 7-field row intraday. Discovery accepts `{PAIR}_full_{tf}.txt` flat or one
  directory down. The pair must have a spec, otherwise that file fails naming the unknown
  pair and the other files still import.
- **Timestamps**: intraday `YYYYMMDD HH:MM:SS` is localised to `America/New_York` with the
  same `ambiguous`/`nonexistent` rules as the futures parser (parity-tested), then
  converted to UTC, stamped at bar open. US DST changes at 02:00 on a Sunday, while the FX
  market is closed (the week opens at 17:00), so real data never hits the ambiguous or
  missing hour. The parity test still pins it. The one off-hour Sunday 02:00 bar in the
  data (2012-07-01) is not on a DST transition date.
- **Daily bars (D5)**: a row dated `D` is stamped at its session open, **`D-1 17:00`
  America/New_York**, converted to UTC (Monday's bar opens Sunday 17:00). This is the same
  bar-open rule as the intraday bars and the futures/equity series, and the daily bar's
  `ts_event` sorts correctly against the hourly bars. Equity daily rows stay at midnight ET.
- **Precision**: prices are rounded half-even to the spec's `price_precision` (5 or 3). This
  pads stripped zeros and removes USDJPY's 2014 float noise. The number of rows whose value
  changed by more than 1e-9 is counted and reported as a warning per file (expected: USDJPY
  only).
- **Validation**: same rules as futures (prices > 0, OHLC consistency, volume >= 0; zero
  volume is valid in FX). Bars are stored as `{ID}-{step}-{agg}-MID-EXTERNAL`.
- **Sessions**: no row is dropped for its weekday. Saturday bars, off-hour Sunday opens and
  Sunday-dated dailies are kept and counted into result warnings (`session_anomalies`) so
  the import is faithful to the source. M6 decides whether a strategy filters them.
- **Volume** is stored as is (tick count) with `info.volume_kind = "tick_count"` on the
  row/instrument. It is documented, not converted.
- **CLI**: `import --format firstrate --asset-class fx --catalog firstrate-fx <dir>`, all five
  timeframes by default (like ETF/futures), `--dry-run` shows per-pair ranges, and the source
  probe accepts both FX row shapes. Memory: the 6M-row 1-minute files stream like futures
  ES (peak measured in the manual task).

## 3. Account-currency conversion (M3)

### The account

FX venues are created with `base_currency=USD` (single-currency margin account). Equity and
futures venues keep exactly today's `add_venue` call (no `base_currency`), so their results
stay byte-identical. A mixed venue (an FX pair plus an equity at one venue) cannot happen:
IDEALPRO only lists FX.

### Rates (D4)

Nautilus' `Cache.get_xrate` (used by the accounts manager for realised P&L, commissions and
margin) builds its table only from **quote ticks or BID+ASK bars** (`_build_quote_table`).
MID bars are ignored. `use_mark_xrates` affects only the portfolio's unrealised view.
Options considered:

1. **Feed synthetic BID and ASK bars** (source +/- half spread). Rates and spread-crossing
   fills would come for free. But the matching engine keeps only one execution bar type per
   instrument and re-picks it whenever a bar type of equal timedelta arrives (`engine.pyx`
   `process_bar`), so three series per pair thrash. This also doubles or triples memory.
   Rejected.
2. **Synthetic quote ticks in the data stream.** The matching engine would execute on them as
   well as on the bars (double processing of the same price). Rejected.
3. **`FxRateFeeder` actor (chosen).** It subscribes to the MID bars of every pair the run
   needs for conversion, and on each bar writes a `QuoteTick(bid=ask=close)` straight into
   the cache (`cache.add_quote_tick`, no message-bus publish, so strategies and the
   matching engine never see it). It also calls `cache.set_mark_xrate` for the portfolio.
   The rate lags the fill by at most one bar (the exchange processes a bar before the data
   engine hands it to actors), which is documented and bounded: under 0.1% on a 1-hour bar
   in normal markets.
4. **Fallback** if the spike shows (3) does not work: keep the venue multi-currency and
   convert in NTrader (extractor and persistence) with the same rate series. This is more
   code in more places, so it is only the backup.

**Which rates a run needs**: for each traded pair with quote `Q != USD`, a pair that converts
`Q` to USD at the same venue. For a USD-base pair (USDJPY) that is the traded pair itself. A
cross (EURGBP) needs GBPUSD, which the loader adds from the same catalog and timeframe as
a **conversion-only instrument** (bars in, no strategy subscription). If the catalog lacks it,
the run fails before the engine starts: `FX conversion pair GBPUSD not in catalog
firstrate-fx`. It never runs unconverted.

**Guard**: after the run, if any FX fill happened while `get_xrate(...)` was `None`, the run
fails loudly. The feeder records misses, so a silently frozen balance (spike item 2) cannot
reach the DB.

### Results in USD

- `backtest_persistence`: a trade's `realized_pnl` string is parsed with its currency. Non-USD
  P&L is converted at the exit-time rate the feeder recorded, and `profit_loss` is stored
  in USD (decision D11, no column). The original quote-currency P&L is recomputable from
  prices and quantity. A non-USD P&L without a rate raises.
- `results_extractor._fills`: commission is USD by construction (section 4). The check
  stays and is now a real invariant.
- `mark_to_market`: accepts `CurrencyPair`s. A position's daily mark is converted with the
  day's rate series (the same bars), so `daily_equity` stays in USD and Sharpe, drawdown and
  other metrics are on USD equity. USD-quoted pairs need no conversion.
  `_check_against_accounts` keeps comparing with `balance_total(USD)`, which is now
  authoritative for FX.
- `config_snapshot` gains `account_currency: "USD"` and `fx_pairs` (per instrument: base,
  quote, pip size, margin %, commission terms, spread, as-of, conversion pair). It is declared on
  `StrategyConfigSnapshot` and omitted when unset (the futures lesson from task 4.5).

## 4. Costs (M4)

### Commission (IB FX, D6)

`FxCommissionModel(FeeModel)` returns **USD** `Money`:

```
trade_value_usd = fill_qty x fill_px x rate(quote -> USD)   # USDJPY: fill_qty (base is USD)
commission      = max(rate_bps / 10_000 x trade_value_usd, min_per_order_usd) x scale
```

IB's minimum is **per order**, but Nautilus calls the fee model per fill. The model keeps the
commission already charged per `client_order_id` and charges each later partial fill only the
top-up above the minimum, so a 3-fill order pays `max(total bps, $2)` once. Bar-based fills
are usually a single fill. `scale` is the run's cost-stress multiplier. Quote-to-USD comes
from the cache rate table (fed by section 3). For USD-quote and USD-base pairs the rate is
closed-form, so the fee model has no dependency on the feeder for majors.

Rates (**UNVERIFIED**): IBKR Pro spot currencies, 0.20 bp x trade value, minimum USD 2.00
per order (tiers down to 0.08 bp above USD 5bn/month; minimum tiers USD 2.00 to 1.00).
Charged in the account base currency. Source: IBKR "Commissions Spot Currencies" page.

### Spread and slippage (D7)

The source is a single series (bid/mid UNVERIFIED), so the bid/ask spread is **modelled**: each
fill is charged `qty x (typical_spread_pips x pip_size) / 2` in the quote currency, converted to
USD, as a separate component of the same fee model. This is the precedent of the equity
`slippage_per_share` drag: fills stay at the bar's prices, and the spread is a cost. It
scales with the cost-stress multiplier, and there is an optional `--spread-pips`
override/`FX_SPREAD_PIPS_<PAIR>` setting. Limits: stops and limits trigger on the mid series, not
the side's quote. `GapAwareFillModel` (weekend gaps) works unchanged because it reasons
on bar opens.

The run records the commission and spread components separately in `config_snapshot.fx_pairs`
and in the report, so their relative weight is visible (for small orders the $2 minimum
dominates: 10k EUR costs about 1.7 bp in commission alone).

## 5. Leverage and margin (M4, D8)

- Margin = `notional_quote x margin_pct` through Nautilus' `LeveragedMarginModel` (the
  default, venue leverage 1, `margin_init` from the instrument), converted to USD by the
  accounts manager with the feeder's rates. Unlike futures, a percentage does not drift with
  price, so no custom model is needed.
- **Enforcement**: Nautilus still does not deny over-margin orders on margin accounts. The
  M3 futures gate is generalised: `FuturesMarginGate` becomes `MarginGate` with a per-instrument
  `required_initial(order_qty, price) -> USD` strategy. Futures keeps `contracts x
  dollars`, FX adds `qty x price x rate x margin_pct`. All rules (new exposure, HEDGING
  closing credit, venue-wide `SUBMITTED` reservations, quantity-increase modify rejection)
  are shared. Futures reason strings and tests are unchanged. The FX deny reason reuses
  `MARGIN_EXCEEDS_FREE_BALANCE`.
- **Implied max leverage** = 100 / margin_pct (about 33:1 at 3%). It is reported per run.
- **Defaults (UNVERIFIED)**: about 3% for EURUSD/GBPUSD/USDJPY at IB (IB publishes per-pair FX
  margin rates; US-regulated retail caps of 2%/5% do not apply to IDEALPRO the same way).
  Same known limits as futures: no unrealised-P&L buying power, margin calls or
  liquidation.

## 6. Position sizing (M4)

Units are **base-currency units** (EURUSD qty 100,000 = EUR 100,000 = 1 standard lot).
Lots are display-only (`units / 100,000`).

New framework-free helpers in `src/core/position_sizing.py`:

- `quote_to_account_rate(instrument, price, rates) -> Decimal`: 1 for a USD quote,
  `1 / price` for a USD base, otherwise the rate table (raises when missing).
- `fx_units_for_notional(notional_usd, price, quote_rate) = notional_usd / (price x quote_rate)`
  (USDJPY: `100k / (157 x 1/157) = 100k` units).
- `fx_units_for_risk(risk_usd, stop_distance, quote_rate) = risk_usd / (stop_distance x
  quote_rate)`.
- `whole_units(units, size_increment) -> int`: floor to the increment, **no minimum of 1**
  (0 = no order). An order below `min_order_usd` is placed but logged once per run as an
  IDEALPRO odd lot (live would fill worse or be rejected).

`sma_crossover` and `buy_and_hold` route FX through these, as they route futures through
`whole_contracts`. The submodule's `_sizing.notional_quantity`/`risk_quantity` gain an FX
branch keyed on `isinstance(instrument, CurrencyPair)` (or `asset_class == FX`), taking the
rate from the strategy's cache. Equity and futures branches are unchanged.

`backtest run --units N` (M5) trades exactly N base units per entry for FX (validated like
`--contracts`: an int >= 1, a strategy config with `units`, an FX symbol, otherwise exit 2).

## 7. Ticks and fills

The price increment is the data grid (`10^-5`, `10^-3`). The IB `min_tick` (0.00005 EURUSD,
0.005 USDJPY, UNVERIFIED) is stored for live. Backtest fills and stops are not rounded to it:
the data is finer than the tick, and rounding would bias half-pip levels. A live order must
round to `min_tick`, which is a live-effort task.

## 8. Rollover / financing (D9: out of scope)

IB IDEALPRO has no tom-next swap. An FX position is a pair of currency balances, and IB
charges or pays **interest on the cash balances** (benchmark rate +/- spread, tiered,
UNVERIFIED). For a multi-day USDJPY long that carry is material (several % a year).
M1-M6 do not model it. Every FX report and `docs/agent/fx-backtesting.md` carry a
"financing not modelled" line, and holding-period stats make the exposure visible. A later
optional `FxCarryModel` (daily accrual at 17:00 ET from a dated rate table) is the natural
extension.

## 9. Sessions, weekends and the daily cutoff

- The trading week is Sunday 17:00 to Friday 17:00 New York. The data has no bars outside it
  apart from the logged anomalies, so the engine simply sees no bars over the weekend. Positions
  stay open across it, and the Sunday open gap is real. `GapAwareFillModel` fills a
  stop gapped through at the Sunday open bar's open.
- The FX trade date is `(ts_ET + 7h).date()`. A helper `fx_trade_date(ts_ns)` lives in
  `src/services/fx/sessions.py` for reporting (daily P&L buckets) and strategies (M6).
- `mark_to_market`'s daily equity for FX runs is cut at 17:00 ET (the trade date), not UTC
  midnight, so a day's FX P&L is not split across two rows. Equity and futures runs are
  unchanged.
- IB's IDEALPRO hours and the daily 17:00-17:15 ET pause (UNVERIFIED) matter for live only.

## 10. Strategy compatibility (M6, submodule PR + pointer bump)

| Strategy | Problem on FX | Fix |
|---|---|---|
| riptide | `_compute_limit_price`/`_compute_stop_price` quantize to `0.01` (EURUSD 1.08431 becomes 1.08) | round with `instrument.make_price()` on the unrounded value (equity output unchanged: equities are 2-decimal instruments) |
| riptide | `_is_liquid` uses `close x volume` = FX tick count x price (JPY "dollars" for USDJPY) | FX config sets `min_adv_dollars: 0`. The strategy skips the ADV check for `CurrencyPair` and logs it once |
| riptide | `notional_quantity` | FX branch (section 6). `--units` support via `RiptideConfig.units` |
| riptide | "MOO" / `max_hold_days` assume daily equity sessions | on 1-day FX bars a "day" is the 17:00 ET trade date, so semantics hold. The README notes it |
| bollinger_reversal | `Price.from_str(f"{x:.2f}")` brackets: precision 2 != 5, so the RiskEngine denies every FX order | `instrument.make_price()` |
| bollinger_reversal | `risk_quantity` ignores quote currency (USDJPY risk off by about 157x) | FX branch with `quote_to_account_rate` |
| bollinger_reversal | weekly SMA buckets by **UTC** ISO week of `ts_event`. Bars stamped at the Sunday 17:00 ET open fall into the previous week | bucket by `fx_trade_date` (NY trade date). For equities that is the same week, which a test pins |
| bollinger_reversal | log lines print `$` | cosmetic: print quote currency |
| both | | configs `configs/riptide_fx.yaml`, `configs/bollinger_fx.yaml`, smoke runs EURUSD/USDJPY 1-day 2015-2025 |

`sma_crossover` (core) and `buy_and_hold` get FX sizing in M4. Other submodule strategies
are listed in `docs/agent/fx-backtesting.md` as unported.

## 11. Reporting, dashboard and CLI (M5)

`src/services/fx/returns.py` (pure, like `futures/returns.py`) works from `config_snapshot.fx_pairs`
and trade rows:

| Figure | Formula |
|---|---|
| units / lots | `quantity`, `quantity / 100,000` |
| notional USD | `quantity x entry x rate_at_entry` (USD base: `quantity`) |
| pips | side-aware `(exit - entry) / pip_size` |
| return on the underlying | side-aware `pips x pip_size / entry` |
| P&L USD | stored `profit_loss` (already converted, M3) |
| margin used | `notional_usd x margin_pct` |
| run: peak notional leverage | `max concurrent notional_usd / equity` (sweep as in futures) |
| run: costs split | commission vs modelled spread, USD |

**Headline (D10)**: the account return (`total_return`, Sharpe, drawdown on USD equity) stays
the headline for FX. Return on margin is shown but not as the headline: with a fixed
percentage margin it is just account-return / (margin% x leverage), so it reads as a
leverage multiplier, not a skill measure. Pips and return on the underlying are shown next
to it.

- CLI `show`/`report`/`history`: an FX block (`EURUSD (fx)`, pips, lots, leverage, costs split,
  "financing not modelled"). Equity and futures output byte-identical.
- MCP `get_run`/`compare_runs`/`export_results`: `asset_class: "fx"`, an `fx` object, FX trade
  columns only when an FX run is exported.
- `backtest run`: `--symbol EURUSD|EUR/USD` resolves to `EUR/USD.IDEALPRO`. `--timeframe 1-HOUR`
  maps to `1-HOUR-MID` for FX catalog rows (looked up before data loads; the `-LAST`
  hardcode in `_backtest_helpers.py` becomes `price_type_for(asset_class)`). `--units N`.
  `--spread-pips` (optional override, D7).
- **Dashboard (needs a FROZEN_VIEWS ruling, D12)**: the same files as the futures ruling, with an FX
  branch on each. `src/api/models/backtest_detail.py` (`view.fx`), new
  `templates/partials/fx_panel.html`, `templates/backtests/detail.html` (include),
  `src/api/ui/backtests.py` and `templates/partials/trades_table.html` (FX columns: Units/Lots,
  Notional $, Pips, Underlying %, P&L $), `src/api/rest/trades.py` (export),
  `src/api/models/backtest_list.py` and `templates/backtests/list_fragment.html` (`FX` badge),
  `src/cli/commands/compare.py`. Equity and futures pages stay byte-identical against goldens
  captured before the change.

## 12. Live-trading compatibility notes (not implemented here)

- **Ids line up**: `EUR/USD.IDEALPRO` is what the IB adapter loads (`load_ids`) and
  subscribes, so a backtest config's `instrument_id`/`bar_type` work live unchanged.
  The adapter's live `CurrencyPair` has `margin_init = 0`, fees 0 and precision from
  `minTick`. NTrader's live path must not rely on those (margin and fees come from the
  account).
- **Bars**: IB has no TRADES for FX. Live bars must be `MID` (IB `MIDPOINT`) to match the
  backtest. `use_regular_trading_hours` must be False for CASH, or the feed stops outside
  "RTH" (UNVERIFIED adapter behaviour). Live session bar-type defaults (`-LAST`) need an
  FX branch.
- **Orders**: round to IB `min_tick` (section 7), respect `min_order_usd` (odd lots) and
  `size_increment`. IDEALPRO rejects or reprices odd lots differently (UNVERIFIED).
- **Positions are cash balances at IB**: IB reports an FX "position" as currency balances
  (plus a virtual FX position in TWS). `live_reconcile`/`reconciliation_service` compare
  positions and USD cash. FX will show JPY/EUR balances and needs its own reconciliation
  rules. `LiveAccountView.from_session` refuses non-USD P&L today, so that would have to be
  changed deliberately.
- **Commissions** are charged in USD but P&L is realised in the quote currency. The live
  trade recorder's `trade.commission_mixed_currency` path will fire for USDJPY, and the
  conversion (section 3) needs a live rate source (IB quotes, so native `get_xrate` works
  live).
- **Guard lists**: any live FX change touches `src/core/live_*` and `EMITTED_*`/
  `NODE_FACING_MODULES` lists (`docs/agent/guard-lists.md`).
- `ibkr_venue_provider.sec_type` and `ibkr_data_provider` need `CASH`. That is also the route
  to IB historical FX data.

## 13. Decisions needing approval (Allay)

| # | Decision | Recommended default |
|---|---|---|
| D1 | Instrument id and symbol input | `EUR/USD.IDEALPRO` (IB-native); catalog `ticker = EURUSD`; CLI accepts both forms |
| D2 | Bar price type | `MID` (`-MID-EXTERNAL`), matching IB MIDPOINT live bars; the matching engine treats MID like LAST |
| D3 | What the FirstRate series is | treat as mid; model the full spread as half-spread per side (D7) |
| D4 | Rate mechanism | USD single-currency FX venues plus the `FxRateFeeder` cache quotes; fallback NTrader-side conversion; never run unconverted |
| D5 | Daily bar timestamp | session open `D-1 17:00 ET` (bar-open rule); the trade date is the 17:00 NY cutoff |
| D6 | Commission | IB 0.20 bp x USD trade value, USD 2.00 minimum per order, in USD, scaled by cost stress |
| D7 | Spread | per-pair `typical_spread_pips` (EURUSD 0.5, GBPUSD 0.8, USDJPY 0.5) charged as a fee drag, with a CLI override |
| D8 | Margin | % of notional (3% majors), enforced by generalising the futures gate |
| D9 | Rollover/financing | out of scope; stated on every FX report; `FxCarryModel` later |
| D10 | Headline metric | account return on USD equity; pips, underlying %, leverage and costs alongside; RoM secondary |
| D11 | Schema | no migration; FX `profit_loss` stored in USD; terms in `config_snapshot.fx_pairs` |
| D12 | Dashboard | pre-approve a FROZEN_VIEWS regeneration for the files in section 11 (same set as the futures ruling plus `fx_panel.html`) |
| D13 | Sizing unit | base-currency units, floored to `size_increment`, no minimum of 1, odd-lot warning; `--units N` |
| D14 | Session anomalies | keep the Saturday/off-hour bars, warn at import; strategies are not filtered |
| D15 | Pair universe | EURUSD, GBPUSD, USDJPY (data) plus EURGBP spec (fixture-tested cross) |
| D16 | OpenSpec layout | one umbrella change, tasks grouped by milestone, each PR ticks its section; archive after M6 |
