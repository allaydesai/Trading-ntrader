# FX backtesting: pairs, instruments, conversion

Spot FX on IB IDEALPRO, planned in `openspec/changes/add-fx-backtesting/` (M1-M6; decisions
D1-D16 approved 2026-11-22). **Status: M1-M4** (specs, instruments, the engine gate, the
FirstRate importer and the `firstrate-fx` catalog, USD account conversion, and costs, margin
and base-unit sizing). An FX run settles in USD end to end through the orchestrator (MCP and
direct callers) and pays IB's spot commission plus a modelled half-spread, under 3% margin.
**Every cost and margin figure is an UNVERIFIED default** (table below). The CLI and web forms
still build `-LAST` bar types, and no report shows FX figures yet (both M5). The sections
marked *(planned)* describe later milestones.

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
  (commission comes from the venue fee model, `FxCommissionModel`, M4);
- `info`: `asset_class`, pip size, min tick, IDEALPRO minimum, spread, margins and as-of,
  commission terms, the `ib` block, and the list of `unverified` fields.

STOCK, ETF, `None` and FUTURES rows build exactly what they did before.

### UNVERIFIED values (Allay to confirm against the IBKR account, task 4.7)

**UNVERIFIED, Allay to confirm.** M4 charges these (commission, spread, margin) and sizes with
them (size increment), so they move every FX result. They are the approved working defaults
(D6-D8, D13), not account figures. Change them in `src/services/fx/pair_specs.yaml` (each has a
`sources` entry with `status`, `source` and `as_of`); every run records the values it used in
`config_snapshot.fx_pairs`.

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
  venue is charged by `FxCommissionModel` (M4, below); a venue mixing FX pairs and other
  instruments is refused.
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
  `"100000.00 JPY"` is replaced by the position's USD as the account booked it (every
  reducing fill converted at its own recorded rate, `usd_realised_pnls`), rounded to cents; a non-USD P&L with no rate fails the trade write. The
  analyzer gets each converted trade in USD, so `total_pnl`, `largest_win`, profit factor and
  the other P&L statistics are USD. Mark-to-market equity for an FX run books each fill's
  realised P&L at its own recorded rate (average cost per position, as Nautilus' `Position`)
  and marks open positions at the latest rate, one value per **17:00 New York trade date**;
  a flat run rebuilds to the engine's USD balance, so `metrics_basis` is `mark_to_market`.
  `config_snapshot` declares `account_currency` and `fx_pairs` (filled by M4, below).
- A position closed in several parts converts each part at its own fill's rate, as the
  account does, so the stored `profit_loss` equals the balance change (pinned: USD 497.24 for
  two halves; halves at 151 and 149 net 0 JPY but USD -4.44, and store -4.44).

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

## Costs, margin and sizing (M4)

Code: `src/core/fx_costs.py` (`FxCommissionModel`, `fx_terms`, `fx_snapshot`),
`src/core/margin_gate.py` (`MarginGate`, `FxRequirement`), `src/core/backtest_venue.py`
(`cost_snapshot`), `src/core/position_sizing.py` (`quote_to_account_rate`,
`fx_units_for_notional`, `fx_units_for_risk`, `whole_units`), `src/core/fx_rates.py`
(`usd_per_quote`, `fx_units`). Tests: `tests/component/core/test_fx_costs.py`,
`tests/component/core/test_fx_strategy_sizing.py`, `tests/unit/test_position_sizing.py`,
`tests/integration/core/test_fx_margin_engine.py` (forked).

### What a fill costs (all UNVERIFIED defaults)

Per fill, in USD, scaled by the run's cost multiplier:

- **Commission**: `max(0.20 bp x USD trade value, USD 2.00)`, **once per order**. Nautilus calls
  the fee model per fill and the bar fill model often splits a 100k order into two or three
  fills, so the model keeps each order's running totals and a later fill pays only the top-up.
  USD trade value: the quantity for a USD-base pair (USDJPY 100,000 = USD 100,000), `quantity x
  price` for a USD quote, and `quantity x price x GBP/USD` for a cross (the cache rate the
  account also uses; a cross fill without one is charged nothing and the run fails on the M3
  guard).
- **Spread** (D3, D7): the bars are treated as mid, so fills stay at the bar's prices and each
  side pays `quantity x typical_spread_pips x pip_size / 2`, converted to USD at the same rate.
  EURUSD 0.5 pip is USD 2.50 per side per 100k (USD 5.00 a round trip); GBPUSD 0.8 is 4.00;
  USDJPY 0.5 pip is 250 JPY per side per 100k (about USD 1.70); EURGBP 1.0 is 5 GBP.
- Both components are rounded to cents once per order (not per fill). The run's totals per pair
  are in `config_snapshot.fx_pairs[<id>].costs_usd` (`commission`, `spread`); M5 reports them.
  For small orders the minimum dominates: 10k EURUSD pays USD 2.00 + 0.25 a side, about 2 bp.
- An order worth less than the IDEALPRO minimum (USD 25,000, UNVERIFIED) is still filled; the
  first one per pair logs `fx_odd_lot` (live it would fill worse or be rejected).

Worked round trips (pinned in the engine test): EURUSD 100,000 at 1.10000 to 1.10100 nets
USD 100.00 - 4.40 - 5.00 = **90.60**; USDJPY 100,000 at 150.000 to 151.000 nets 662.25 - 4.00 -
3.33 = **654.92**; EURGBP 100,000 at 0.85000 to 0.86000 (GBPUSD 1.25) nets 1,250.00 - 4.27 -
12.50 = **1,233.23**.

Trade rows: a USD-quote pair's `realized_pnl` is already net of its USD commission (Nautilus
nets a same-currency commission). For a pair settling outside USD Nautilus does not net it,
so the USD `profit_loss` M3 rebuilds also deducts each fill's USD commission. Either way the
sum of the trade rows equals the USD balance change, and mark-to-market reconciles.

### Margin (D8)

- Initial and maintenance margin are `margin_pct` (3%, UNVERIFIED) of USD notional, through
  Nautilus' default `LeveragedMarginModel` and the pair's `margin_init`/`margin_maint`. FX venues
  are added with `default_leverage=1`: Nautilus' margin-account default of 10 would have turned
  3% into 0.3%. The locked margin is in USD (converted with the feeder's rates for USDJPY and
  crosses). Implied maximum leverage is 100 / 3 = 33x.
- **Enforced** by the futures gate generalised to `MarginGate`: one per-instrument requirement
  (`FuturesRequirement`: dollars per contract; `FxRequirement`: `units x price x USD per quote
  unit x margin_pct`). An entry needing more than the free USD balance is denied
  `MARGIN_EXCEEDS_FREE_BALANCE: 400000 EUR/USD.IDEALPRO unit(s) need 13200.00 USD initial
  margin, free=10000.00 USD`; closes are credited and never denied; a quantity-increase modify
  is rejected `FX_QTY_INCREASE_NOT_SUPPORTED` (and `FX_MODIFY_PENDING` while one is open), and
  a new limit or trigger that raises an entry's margin by more than is free is rejected
  `MARGIN_EXCEEDS_FREE_BALANCE` (futures margin ignores price, so their reprices pass). The
  increase is computed on the order's full quantity, as Nautilus margins it, and a
  margin-raising reprice is refused `FX_MODIFY_PENDING` while another FX modify on the venue is
  not yet applied (its increase is not reserved yet). "Not yet applied" is read from each
  order's events, not its status: Nautilus restores an order's status when a later modify is
  rejected even while an earlier one is still queued. An `OrderUpdated` counts as the answer to
  a modify only on a plain order; on order kinds Nautilus can update unsolicited (trailing
  stops, market-to-limit, reduce-only, contingent, emulated and exec-algorithm orders) only a
  rejection or the order closing resolves it. When in doubt the modify stays pending, so a
  resize or margin-raising reprice waits rather than overcommitting margin (no strategy in the
  repo modifies orders today). The same counting backs the one-quantity-modify-at-a-time rule
  for futures and FX. A new FX entry also waits (`FX_MODIFY_PENDING`) while any margined FX
  entry (parentless, not reduce-only) on the venue has an unresolved modify: the gate cannot
  know how much margin that pending reprice adds (no per-order state; Nautilus keeps the
  requested price only in the exchange's queue), so it counts it as committed in full. Resizes
  need no such check: increases are always refused and decreases release margin. The
  reference price is the order's limit or stop trigger, else the latest `MID` price. Futures
  names, reasons and behaviour are unchanged (`src/core/futures_margin_gate.py` keeps them).
- Limit: an order the gate cannot price (no bar yet, or a cross before its first GBPUSD bar) is
  passed through (its priced siblings in an order list are still checked); a fill in that state has no rate either and the M3 guard fails the run, but a
  resting order submitted then and filled later goes unchecked. Same known limits as futures: no
  unrealised P&L in buying power, no margin calls or liquidation.

### Sizing (D13)

Units are base-currency units (EURUSD 100,000 = EUR 100,000; lots are display-only, M5).
`fx_units_for_notional(notional_usd, price, quote_rate) = notional_usd / (price x quote_rate)`,
with `quote_rate` 1 for a USD quote, `1 / price` for a USD base and the cache rate for a cross,
floored to `size_increment` (UNVERIFIED: 1) with no minimum of one (0 is no order).
`sma_crossover` and `buy_and_hold` size FX this way: USD 100,000 of USDJPY is 100,000 units (M3
sized ~690, the equity `notional / price`). CLI mode passes the starting balance as
`portfolio_value` and 10% a trade, so a USD 1M account trades 100,000 units. `buy_and_hold`
reads the account's USD cash (an FX account holds no JPY). `contracts` stays futures-only.
Submodule strategies (Riptide, Bollinger Reversal) size FX through `_sizing.py` since M6
(see "Strategies on FX").

### Smoke (task 4.8, 2026-11-22)

`sma_crossover` USDJPY 1-HOUR-MID, 2024-01-02 to 2024-02-01 (end-of-day inclusive),
`firstrate-fx`, USD 1M, persisted, M3 code against M4 code:

| | M3 (before) | M4 (after) |
|---|---|---|
| Size per trade | 675-691 units | 100,000 units |
| Trade rows (closed + open) | 12 (11 + 1) | 12 (11 + 1) |
| USD P&L (sum of rows = balance change) | -9.69 | -1,532.09 |
| Commission / spread | 0.00 / 0.00 | 46.00 / 39.13 (23 orders) |
| `config_snapshot.fx_pairs` | absent | filled |

Trade 1 by hand: short 100,000, filled 3,118 at 144.657 and 96,882 at 144.656 (avg
144.65603118), covered 6,857 at 143.908 and 93,143 at 143.909 (avg 143.90893143): 74,709.975 JPY.
Each covering fill converts at the cache rate 1/143.908: USD 519.15. Costs: commission 2.00 + 2.00
(0.2 bp x USD 100,000 is exactly the minimum); spread 250 JPY / ~144.656 = 1.73 and 250 /
~143.909 = 1.74; the fills were charged 2.05 + 1.68 and 2.12 + 1.62 = 7.47. Net **511.68**,
stored 511.68.

2024 H1 (2024-01-02 to 2024-06-28, same setup), each sum of trade rows equal to the balance
change, `metrics_basis` `mark_to_market`:

| Pair | Trade rows | USD P&L | Commission | Spread |
|---|---|---|---|---|
| EURUSD | 87 | -5,517.65 | 346.07 | 399.81 |
| USDJPY | 77 | -2,234.33 | 306.00 | 250.93 |

EURUSD trade 1 by hand: long 91,239 units (USD 100,000 / 1.09602, floored), filled 2,119 at
1.09602 and 89,120 at 1.09603, sold 2,611 at 1.09167 and 88,628 at 1.09166: -43.69 pips, USD
-398.66. Costs 8.56 (commission 2.00 a side: 0.2 bp of ~USD 100,000 and ~99,601 both round to
the minimum; spread 91,239 x 0.000025 = 2.28 a side). Net **-407.22**, stored -407.22.

## Reporting, CLI and dashboard (M5)

`src/services/fx/returns.py` (pure, like `futures/returns.py`) computes everything from a run's
`config_snapshot.fx_pairs` and its trade rows; `src/services/fx/display.py` words it once for
every renderer. A run without `fx_pairs` is not FX and every renderer prints what it did before.

| Figure | Definition |
|---|---|
| units / lots | `quantity`, `quantity / 100,000` |
| USD notional | USD quote: `quantity x entry`; USD base (USDJPY): `quantity`; a cross: not shown (its quote/USD rate at entry is not stored) |
| margin used | `notional x margin_pct` |
| pips | side-aware `(exit - entry) / pip_size` |
| underlying % | side-aware `(exit - entry) / entry` (the stored `profit_pct` is not side-aware for shorts) |
| P&L USD | the stored `profit_loss` (converted and net of USD costs since M3/M4) |
| peak leverage | max concurrent USD notional / (starting balance + P&L of trades closed so far) |
| costs split | `fx_pairs[...].costs_usd.commission` vs `.spread`, summed over pairs |

The account return on USD equity stays the headline (D10). Return on margin is shown as
secondary. Every FX view carries `FINANCING_NOTE` (D9: rollover is not modelled).

- **CLI**: `backtest show` and `report summary` print an FX table after the account metrics;
  `report` text/CSV/JSON add it (`fx_*` CSV rows, a JSON `fx` object with per-trade figures);
  `history` marks `EURUSD (fx)` and keeps the account return; `compare` adds pips, peak
  leverage and total costs (and the asset class row).
- **MCP**: `get_run`/`compare_runs` rows have `asset_class: "fx"` and an `fx` object plus a
  warning; `export_results` adds the FX trade columns only for FX runs. The mixed-asset-class
  warning fires only when futures is in the set (FX and equity share the account return).
- **`backtest run`** (and config mode, the MCP `resolve` path and the dashboard's Run form):
  `EURUSD`, `EUR/USD`, `eur/usd` and `EUR.USD` name the catalog ticker `EURUSD`; an FX catalog
  row (`asset_class FX`) loads `MID` bars from `--timeframe` (`src/services/fx/symbols.py`).
  Only a symbol naming a shipped pair spec is looked up, so equity and futures runs never
  touch the DB for it. `--units N` (N >= 1) sets `SMAConfig.units`: exactly N base units per
  entry, FX only (exit 2 otherwise, like `--contracts`). `--spread-pips X` rebuilds the pair
  with `typical_spread_pips = X` (`BacktestRequest.fx_spread_pips`, hashed only when set; it
  shows in `fx_pairs[...].typical_spread_pips`). The `FX_SPREAD_PIPS_<PAIR>` env setting the
  design mentions is not implemented (an env value outside the config hash would make runs
  unreproducible). The CLI now passes a cross's conversion bars (`rate_sources`) too.
- **Dashboard** (D12 FROZEN_VIEWS ruling): a Spot FX panel after the metrics, an FX trades
  table (units, lots, pair-precision prices, USD notional, pips, underlying %, USD P&L), FX
  export columns, an `FX` badge in the run list. Equity and futures pages are pinned byte for
  byte against goldens captured before M5 (`tests/component/api/test_fx_dashboard.py`).
- **Import summary**: `ImportResult.ohlc_flags` separates OHLC flags from the FX/futures
  counters (rounding, session anomalies, out-of-order rows), which get an "Other warnings"
  row; the FX dry-run labels timeframes `-MID`.

### Smoke (task 5.7, 2026-11-23)

CLI, `firstrate-fx`, `sma_crossover`, 1-HOUR, 2024-01-02 to 2024-02-01, persisted:

| Run | Input | Result |
|---|---|---|
| `226b08b2` | `--symbol USDJPY` (CLI default USD 10M) | 1-HOUR-MID bars, 12 trade rows, total return -0.15% |
| `573c5ed6` | `--symbol EUR/USD --starting-balance 1000000 --units 50000 --spread-pips 1.0` | ticker EURUSD; 16 closed; every trade 50,000 units; commission USD 66.00 (33 orders x 2.00 minimum); spread USD 82.50 (50,000 x 1.0 pip x 0.0001 / 2 = 2.50 a side x 33); peak notional USD 54,913.99, leverage 0.05x |
| `4d7ce72b` (MCP `submit_backtest`, stdio) | `symbol "EUR/USD"`, USD 1M | resolved `EURUSD` / `1-HOUR-MID`; `get_run` `asset_class fx`, 16 closed, peak notional USD 100,000.84 (10% of 1M), leverage 0.10x, commission 66.00, spread 75.62 |

`AAPL --units 1000` exits 2 ("--units is for FX only").

## Strategies on FX (M6)

Ported: `sma_crossover`, `buy_and_hold` (core, M4), `riptide` and `bollinger_reversal`
(private submodule, M6). Configs: `configs/riptide_fx.yaml`, `configs/bollinger_fx.yaml`
(EURUSD 1-DAY MID, 2015-2025, USD 1M); `--symbol USDJPY` switches the pair, `--units N` trades
a fixed size:

```bash
uv run python -m src.cli.main backtest run configs/bollinger_fx.yaml --catalog firstrate-fx
uv run python -m src.cli.main backtest run configs/riptide_fx.yaml --catalog firstrate-fx --symbol USDJPY
```

What the submodule strategies do on a spot pair (`is_fx_pair`, design section 10):

| | Riptide | Bollinger Reversal |
|---|---|---|
| Sizing (`_sizing.py`) | `position_size_pct` of `portfolio_value` in USD -> base units (`fx_units_for_budget`) | `max_risk_pct` of `portfolio_value` over the 2 x ATR stop, priced in USD (`fx_units_for_risk_budget`: USDJPY yen at `1/price`, exact for a USD base) |
| Rounding | floor to `size_increment`, no minimum of 1 (0 = no order); `units` replaces the budget (FX only, N >= 1) | same |
| Prices | FX: limit and ATR stop half-up to the pair's precision; everything else cents, as before | FX: bracket stop/target at the pair's precision (`f"{x:.{precision}f}"`); everything else 2 dp, as before |
| FX-only behaviour | ADV dollar-volume filter skipped (FirstRate volume is a tick count), logged once on start | weekly SMA buckets by `fx_trade_date` (the Sunday 17:00 ET bar opens the new week); log lines print the quote currency |
| Cross pairs | the quote/USD rate from the cache (`fx_quote_rates`); none yet raises, as `sma_crossover` | same |

A "day" on 1-day FX bars is the 17:00 New York trade date (daily rows are stamped at the
session open, D-1 17:00 ET), so Riptide's next-open exits and `max_hold_days` keep their
meaning. `riptide_fx.yaml` sets `limit_offset_pct: 0.5`, not the equity 2.0: 2% is about three
daily ATRs on EURUSD, and every one of 80 EURUSD setups (2015-2025) expired unfilled at 2%
(USDJPY: 2 trades from 67 setups). UNTUNED working default, Allay to confirm.

`make_price` is not used for these prices although design section 10 names it: it rounds
half-even (100.125 -> 100.12, where Riptide's cents rounding gives 100.13) and its float path
differs from `f"{x:.2f}"` (100.135 -> 100.14 vs 100.13). The precision change is FX-only:
`firstrate-stocks` equities are price precision **4** (not 2, as section 10 assumed), and
rounding them at 4 dp moved Riptide AAPL 2015-2024 from 3.50% to 3.49%. With the FX gate both
strategies' AAPL 1-day 2015-2024 results are byte-identical to the pre-M6 submodule.

**Pre-existing equity bug (not fixed here, open for Allay):** `bollinger_reversal`'s 2-dp
brackets are rejected on a precision-4 catalog equity ("Invalid trigger price precision ...
was 2 when AAPL.NASDAQ price precision is 4"), so it trades nothing on `firstrate-stocks`
(AAPL 2015-2024: 0 trades). Using the instrument's precision for every instrument fixes it
(82 trades, +1.57%) but changes equity results, which M6's acceptance forbids.

**Not ported** (still size FX like equities, `notional / price` with a minimum of one, and
format prices at 2 dp where they set any): `sma_crossover_long_only`, `apolo_rsi`,
`mean_reversion` (`rsi_mean_reversion`), `connors_rsi_mean_rev`, `momentum` (`sma_momentum`).
Port one by routing its size through `_sizing` (submodule) or `fx_units` (core) and its prices
through the instrument's precision, as above.

### Smoke (task 6.5, 2026-11-23)

CLI config mode, `firstrate-fx`, 1-DAY MID, 2015-01-02 to 2025-12-31, USD 1M, persisted. No
order was denied in any run.

| Run | Strategy / pair | Trades | Return | Notes |
|---|---|---|---|---|
| `be0d982a` | bollinger_reversal EURUSD | 92 | +24.35% | largest loss USD 10,124.23 (the 1% risk plus costs) |
| `02be82e5` | bollinger_reversal USDJPY | 104 | +3.29% | largest loss USD 10,265.73 |
| `2b0d081e` | riptide EURUSD | 16 | +0.21% | every entry ~USD 100,000 (e.g. 90,957 units at 1.09942) |
| `7388099a` | riptide USDJPY | 24 | -0.08% | every entry 100,000 units; -0.09% (USD 999,093.44) after the QA stop fix below |

Hand-checked (to the cent, stored `profit_loss`):

- **riptide USDJPY, trade 1** (2017-01-16 -> 01-18): setup close 114.182, limit
  `114.182 x 0.995 = 113.611` (3 dp), 100,000 units (USD 100,000 of a USD-base pair). Stop
  `113.611 - 2.5 x 1.4273 = 110.04275 -> 110.043`. Exit 114.660 (green-candle next open).
  Gross `1.049 x 100,000 = 104,900 JPY = USD 914.88` at 1/114.66; costs `2.00 + 2.00`
  commission (0.2 bp of USD 100,000 = the USD 2 minimum) `+ 2.20 + 2.18` half-spread (250 JPY
  a side); net **906.50**.
- **bollinger_reversal EURUSD, trade 1** (2015-05-25 -> 06-17): close 1.08699, 2 x ATR =
  0.0262815, so `10,000 / 0.0262815 = 380,496` units, SL 1.06071, TP 1.13955 (5 dp). Filled
  at 1.08699802 (the bar fill model splits the order), exit at the TP. Gross
  `0.05255198 x 380,496 = 19,995.82`; commission `8.27 + 8.67` (0.2 bp of USD 413,598 /
  433,594) plus half-spread `9.51 x 2`, total 35.96; net **19,959.86**.

**Stop resize (FX QA, 2026-11-23, strategies PR #5).** The bar matcher offers a quarter of a
bar's volume at each OHLC point, so an entry larger than that fills in parts; FX volume is a
tick count, so most daily FX entries split (USDJPY 2023-07-10: 81,022 + 18,978). Riptide sized
its hard stop from the first part: 13 of 16 EURUSD and 8 of 24 USDJPY stops covered part of the
position, and a USDJPY stop-out left 18,978 units open to the next green candle. It now
replaces the stop with one for the whole position when the entry keeps filling
(`on_position_changed`). Bollinger's bracket stops carry the full order quantity (QA: 198 of 198) and are
not affected. Details: `docs/agent/fx-qa-2026-11-23.md`.

Found by the smoke and fixed in the submodule: a YAML config's numbers reach
`BollingerReversalConfig` as float, and `Decimal x float` crashed the first entry of every
config-file run (Riptide already coerced them).
