# FX backtesting: pairs, instruments, conversion

Spot FX on IB IDEALPRO, planned in `openspec/changes/add-fx-backtesting/` (M1-M6; decisions
D1-D16 approved 2026-11-22). **Status: M1 only** (specs, instruments, the engine gate). There
is no FX importer, catalog, cost model or reporting yet, and `import --asset-class fx` still
exits 2. The sections marked *(planned)* describe later milestones.

**Financing is not modelled** (D9): IB charges or pays interest on currency balances, and no
backtest includes it.

## Ids and symbols (D1)

| Pair | Instrument id | `raw_symbol` | Catalog `ticker` | Bars (M2) |
|---|---|---|---|---|
| EURUSD | `EUR/USD.IDEALPRO` | `EUR.USD` | `EURUSD` | `EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL` |
| GBPUSD | `GBP/USD.IDEALPRO` | `GBP.USD` | `GBPUSD` | same pattern |
| USDJPY | `USD/JPY.IDEALPRO` | `USD.JPY` | `USDJPY` | same pattern |
| EURGBP | `EUR/GBP.IDEALPRO` | `EUR.GBP` | (no data; fixture-only cross) | |

The id is exactly what the Nautilus IB adapter's IB_SIMPLIFIED symbology produces and parses
(`RE_CASH`, `VENUES_CASH = ["IDEALPRO"]`), so a backtest bar type names the same instrument
as a live subscription (pinned in `tests/component/services/fx/test_instrument_builder.py`).
On disk Nautilus strips the `/` (`EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL`).

## Pair specs

`src/services/fx/pair_specs.yaml`, loaded and validated by `src/services/fx/pair_specs.py`
(`load_pair_specs`, `get_pair_spec`, `get_commission_spec`; errors are `FxSpecError` naming
the file and the dotted field). Per pair: base, quote, venue, pip size, data price precision,
IB min tick, size increment, IDEALPRO minimum (USD), modelled spread (pips), initial and
maintenance margin (% of notional), session, the IB `CASH` block, and a `sources` entry for
every economic value (`status: VERIFIED | UNVERIFIED`, `source`, `as_of`). One commission
schedule (bps of USD trade value, USD minimum per order) covers all pairs.

`build_instrument("FX", "EUR/USD.IDEALPRO", "EURUSD", bars)` (in
`src/services/firstrate/backtest_loader.py`) calls `build_fx_pair`
(`src/services/fx/instrument_builder.py`), which returns a Nautilus `CurrencyPair`:

- price precision from the bars (else the spec: 5, or 3 for USDJPY), increment `10^-precision`
  (the data grid; the IB min tick is in `info` for live orders only);
- `size_increment` 1 (whole base units), `margin_init`/`margin_maint` = spec % / 100, fees 0
  (commission will come from the venue fee model, M4);
- `info`: `asset_class`, pip size, min tick, IDEALPRO minimum, spread, margins and as-of,
  commission terms, the `ib` block, and the list of `unverified` fields.

STOCK, ETF, `None` and FUTURES rows build exactly what they did before.

### UNVERIFIED values (confirm against the IBKR account before M4 merges, task 4.7)

| Value | EURUSD | GBPUSD | USDJPY | EURGBP |
|---|---|---|---|---|
| min tick | 0.00005 | 0.00005 | 0.005 | 0.00005 |
| size increment | 1 | 1 | 1 | 1 |
| IDEALPRO minimum (USD) | 25,000 | 25,000 | 25,000 | 25,000 |
| typical spread (pips) | 0.5 | 0.8 | 0.5 | 1.0 |
| initial / maintenance margin | 3% / 3% | 3% / 3% | 3% / 3% | 3% / 3% |
| price precision | measured | measured | measured | **assumed** (no data) |
| commission (all pairs) | 0.20 bp x USD trade value, USD 2.00 minimum per order | | | |

Data facts that are also UNVERIFIED (FirstRate does not document them): whether the series is
bid, ask or mid (treated as mid, D3), and that volume is a tick count.

## Account-currency conversion: the M1 gate result

`tests/integration/core/test_fx_engine_acceptance.py` (forked) runs a real `BacktestEngine`
with a `MARGIN` venue, `base_currency=USD`:

- The venue accepts all four pairs and their `MID` bars; the account only ever holds USD.
- **Without a rate**, a USDJPY round trip (100,000 at 150.000 to 151.000) realises 100,000 JPY
  on the position, logs `Cannot calculate account state: insufficient data for JPY/USD` and
  **leaves the USD balance unchanged**. That is a silent wrong result, so M3's guard must fail
  any run whose FX fill had no rate.
- **With a cache-only rate feeder** (an actor writing `QuoteTick(bid=ask=close)` per MID bar
  with `cache.add_quote_tick`, plus `cache.set_mark_xrate`), the USD balance moves by
  exactly USD 662.25 (100,000 JPY / 151.000, the closing fill's rate). An EURGBP round trip
  (1,000 GBP) converts through GBPUSD, which is loaded as a conversion-only instrument (bars,
  no strategy), to USD 1,250.00.

So decision D4's mechanism works as planned and M3 needs no fallback. Three constraints carry
into M3:

1. Nautilus' `Cache.get_xrate` reads only cache quote ticks (or BID+ASK bar pairs), never MID
   bars, so the feeder is required: no feeder means no conversion.
2. The feeder must be registered **before** the strategies (`add_actor` before
   `add_strategy`), so on each bar it writes the rate before a strategy submits. In the gate
   test the closing market order, submitted from `on_bar`, used that bar's rate (no lag).
   This covers only orders submitted from strategy callbacks. A **resting** order (stop,
   limit) is matched by the exchange before the bar reaches any actor, so its fill converts
   at the previous bar's rate (the one-bar lag `design.md` section 3 allows; e.g. 100,000 JPY
   at 150 instead of 151 is USD 666.67 instead of 662.25). Untested in M1: M3 must pin it.
3. A cross's conversion pair must be added to the engine **before** the traded pair's bars.
   At equal timestamps the engine dispatches bars in the order the data was added. With
   GBPUSD added after EURGBP, the EURGBP close converted at the previous bar's GBPUSD rate
   (USD 1,220 instead of 1,250 on a rate moving 1.22 to 1.25). Both orders are pinned in the
   gate test. USD-quote and USD-base pairs are their own rate source, so rule 3 does not
   apply to them (rule 2's resting-order lag still does).

## Planned (M2-M6)

- M2: `FirstRateFxParser`/`FxImportService`, catalog `firstrate-fx`, MID bars, daily bars at
  `D-1 17:00 ET`.
- M3: `src/core/fx_rates.py` (`FxRateFeeder`), USD venues for FX only, USD `profit_loss`,
  mark-to-market on USD equity.
- M4: `FxCommissionModel` (bps + minimum + modelled half-spread), percentage margin enforced by
  a generalised margin gate, base-unit sizing.
- M5: reporting, dashboard, `--units`. M6: Riptide and Bollinger Reversal on FX.
