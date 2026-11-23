# FX backtesting: pairs, instruments, conversion

Spot FX on IB IDEALPRO, planned in `openspec/changes/add-fx-backtesting/` (M1-M6; decisions
D1-D16 approved 2026-11-22). **Status: M1-M3** (specs, instruments, the engine gate, the
FirstRate importer and the `firstrate-fx` catalog, and USD account conversion). An FX run now
settles in USD end to end through the orchestrator (MCP and direct callers), but FX **costs are
zero** until M4, and the CLI and web forms still build `-LAST` bar types (M5 adds `MID`). The
sections marked *(planned)* describe later milestones.

## Data: the `firstrate-fx` catalog (M2)

`import --format firstrate --asset-class fx --catalog firstrate-fx /srv/market-data/raw/fx`
(see `docs/agent/data-pipeline.md`). Imported 2026-11-22, row counts equal to the source line
counts:

| Pair | 1min | 5min | 30min | 1hour | 1day |
|---|---|---|---|---|---|
| EURUSD | 6,050,178 | 1,215,629 | 202,793 | 101,471 | 4,241 |
| GBPUSD | 6,042,317 | 1,214,776 | 202,730 | 101,452 | 4,239 |
| USDJPY | 6,044,472 | 1,214,696 | 202,756 | 101,468 | 4,240 |

Intraday bars run 2010-01-03 17:00 ET to 2026-04-30 23:59 ET (1-hour to 23:00); daily bars
open 2010-01-03 17:00 ET (row `20100104`) to 2026-04-30 17:00 ET (row `20260501`, partial).
Warnings kept on import: USDJPY rounded rows 16 (1h) / 38 (30m) / 215 (5m) / 1,017 (1m);
session anomalies per intraday timeframe EURUSD 6, GBPUSD 13, USDJPY 7, and Sunday-dated
daily rows EURUSD 2, USDJPY 1. Volume is FirstRate's (almost certainly a tick count,
UNVERIFIED).

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
   That covers orders filled from strategy callbacks only; orders the exchange matches
   itself can lag a bar (resolved in M3, see "When a fill converts" below).
3. A cross's conversion pair must be added to the engine **before** the traded pair's bars.
   At equal timestamps the engine dispatches bars in the order the data was added. With
   GBPUSD added after EURGBP, the EURGBP close converted at the previous bar's GBPUSD rate
   (USD 1,220 instead of 1,250 on a rate moving 1.22 to 1.25). Both orders are pinned in the
   gate test. A USD-base pair (USDJPY) is its own rate source, so the order between pairs
   does not arise for it; a USD-quote pair needs no rate at all.

M3 implements all three (`setup_backtest_venues` enforces 2 and 3).

## USD account conversion (M3)

Code: `src/core/fx_rates.py` (`FxRateFeeder`, `conversion_pair`, `add_fx_data`),
`src/core/fx_results.py` (the guard and exit rates), `src/core/backtest_venue.py`,
`src/core/mark_to_market.py` (`fx_daily_equity`), `src/services/fx/sessions.py`.
Tests: `tests/component/core/test_fx_rates.py`,
`tests/integration/core/test_fx_conversion_engine.py` (forked), `tests/unit/core/test_mark_to_market_fx.py`,
`tests/unit/services/test_backtest_persistence_fx.py`.

- **The account.** A venue listing spot FX pairs (a `CurrencyPair` whose `info.asset_class` is
  `FX`; Kraken crypto pairs are not) is added with `base_currency=USD`: every balance change is
  in USD. Equity and futures venues get exactly the `add_venue` call they had (pinned). An FX
  venue is charged nothing (`FxNoCommissionModel`, USD 0.00) until M4's cost models; a venue
  mixing FX pairs and other instruments is refused.
- **Which rates a run needs.** A USD-quote pair (EURUSD, GBPUSD) needs none: its P&L is
  already USD. A USD-base pair (USDJPY) is its own rate source. A cross (EURGBP) needs
  `{quote}USD` or `USD{quote}` (GBPUSD), which `load_from_catalog` loads from the same catalog
  and bar spec as conversion-only data (`DataLoadResult.rate_sources`; bars in, no strategy).
  A catalog without it fails the load naming GBPUSD; an orchestrator run of a cross with no rate
  sources fails before the engine starts (`FxConversionPairError`). Only the MCP worker passes
  rate sources today; the CLI and web paths have no FX bar type until M5 (crosses have no data
  yet anyway).
- **The feeder.** `setup_backtest_venues(..., data=..., rate_sources=...)` adds the bars itself:
  conversion-only rate sources first, then traded rate sources, then the rest, and registers
  `FxRateFeeder` (only when a pair settles outside USD) **before** the caller adds strategies.
  On each rate-source `MID` bar the feeder writes `QuoteTick(bid=ask=close)` into the cache
  (`cache.add_quote_tick`, never published: strategies subscribed to quotes receive none) and
  sets the mark xrate. It also checks every fill of a converting pair against
  `cache.get_xrate(venue, quote, USD, BID|ASK)`, the accounts manager's exact lookup, recording
  the rate used or a miss.
- **Never unconverted.** A miss means Nautilus logged "Cannot calculate account state" and left
  the USD balance unchanged for that fill. `ResultsExtractor.extract_results` (via
  `settle_fx_results`) then raises `FxRateMissingError`, so the run fails (and is recorded as
  failed when persisting) instead of reporting a frozen balance. FX fills with no feeder at
  all fail the same way.
- **Results in USD.** Trade rows store `profit_loss` in USD: the positions report's
  `"100000.00 JPY"` is converted at the rate(s) the account used for the position's
  reducing fills (its exit, for a single exit), rounded to cents; a non-USD P&L with no rate fails the trade write. The
  analyzer gets each converted trade in USD, so `total_pnl`, `largest_win`, profit factor and
  the other P&L statistics are USD. Mark-to-market equity for an FX run books each fill's
  realised P&L at its own recorded rate (average cost per position, as Nautilus' `Position`)
  and marks open positions at the latest rate, one value per **17:00 New York trade date**;
  a flat run rebuilds to the engine's USD balance, so `metrics_basis` is `mark_to_market`.
  `config_snapshot` declares `account_currency` and `fx_pairs` (filled from M4).
- A position closed in several parts converts each part at its own fill's rate, as the
  account does: the stored `profit_loss` uses the P&L-weighted rate of its reducing fills
  (`exit_rates`), so it equals the balance change (pinned: USD 497.24 for two halves).

### When a fill converts (the rate lag, pinned)

A fill converts at **the latest rate-source bar close the feeder has handled when the fill
happens**. Within one timestamp the engine hands each bar first to the simulated exchange
(which matches resting orders against it) and only then to the data engine (feeder, then
strategies), and it handles bars in the order they were added (rate sources first). So, with
bar N the bar being processed:

| Fill | Fills at | USDJPY (own rate source) converts at | EURGBP (GBPUSD added first) converts at |
|---|---|---|---|
| Market order from `on_bar(N)`, default fill model | bar N's close | bar N's close (exact: 1 / fill price) | GBPUSD bar N's close |
| Resting limit or stop matched by bar N | within bar N | **bar N-1's close** | GBPUSD bar N's close |
| Gap-aware venue: any order released at the start of bar N (signalled on N-1), e.g. an MOO exit at bar N's open | bar N's open (MOO), or bar N-1's close (untagged market) | **bar N-1's close** | **GBPUSD bar N-1's close** |

Worked numbers from the tests (100,000 USDJPY bought at 150.000, sold at 151.000, previous bar
150.500; JPY P&L 100,000): market exit USD 662.25 (1/151.0); resting limit or gap-aware MOO exit
USD 664.45 (1/150.5). EURGBP (P&L 1,000 GBP, GBPUSD 1.22 then 1.25): market and resting exits
USD 1,250.00; a gap-aware release USD 1,220.00.

So with a gap-free rate series the lag is at most one bar of the **rate's** move, and only for
pairs settling outside USD (USDJPY, crosses). The feeder does not check a rate's age: if the
rate source is missing bars (a gap in a cross's GBPUSD series), the cache keeps the last rate
it had, and a fill after the gap converts at that older rate, so the lag can exceed one bar.
Gap-aware runs refuse a rate source whose timestamps differ from the traded pair's;
default-model runs accept it. It never touches fill prices or EURUSD/GBPUSD. On 1-hour bars it is
typically under 0.1% of the trade's P&L. A gap-aware Sunday-open fill converts at the
Friday close, so a weekend gap in USDJPY is the worst case. M3 does not close it: the rate at
an intrabar fill is not knowable from one `MID` bar without looking ahead.

## Planned (M4-M6)

- M4: `FxCommissionModel` (bps + minimum + modelled half-spread), percentage margin enforced by
  a generalised margin gate, base-unit sizing, `config_snapshot.fx_pairs` filled.
- M5: reporting, dashboard, `--units`, `MID` bar types from the CLI. M6: Riptide and Bollinger
  Reversal on FX.
