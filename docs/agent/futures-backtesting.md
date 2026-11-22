# Futures backtesting: margin, commission, sizing, returns

Futures M3. A backtest on a `FuturesContract` (catalog `firstrate-futures`, ids `ES.CME`,
`NQ.CME`, `CL.NYMEX`, `GC.COMEX`, `ZN.CBOT`) differs from an equity backtest in four places.
Every number comes from `src/services/futures/contract_specs.yaml`; the M1 builder copies it
onto the instrument (`instrument.info`), and `src/core/futures_costs.py` reads it from there.

| | Equity | Futures |
|---|---|---|
| Commission | IBKR per share, $1 min, 0.5% cap (`IBKRCommissionModel`) | spec `commission_per_contract_usd` x contracts, per side (`FuturesCommissionModel`) |
| Margin | Nautilus default (none for `Equity`) | spec dollars per contract (`FuturesMarginModel`), enforced pre-trade (`FuturesMarginGate`) |
| Exposure per unit | price | price x multiplier (ES 50, NQ 20, CL 1000, GC 100, ZN 1000) |
| Quantity | whole shares, at least 1 | whole contracts, may be 0 (no order) |

Venue setup lives in `src/core/backtest_venue.py` (`setup_backtest_venues`). A venue with no
futures contract gets exactly the `add_venue` call it always had.

## Margin

- **Model.** Nautilus 1.220 lets a venue take a custom `MarginModel`. `FuturesMarginModel`
  charges `contracts x margin.initial_usd` for a working order and
  `contracts x margin.maintenance_usd` for an open position, at any price. There is no
  per-run reference price, so no drift as the price moves: dollar margins stay dollars.
- **Enforcement.** Nautilus computes and locks that margin but never refuses an order over it
  on a margin account (its risk engine skips margin accounts, and the account clamps free
  balance at zero). `FuturesMarginGate` wraps the `RiskEngine.execute` message-bus endpoint,
  which every order path goes through. It denies an order with Nautilus' own `OrderDenied`
  (reason `MARGIN_EXCEEDS_FREE_BALANCE: ...`) when the new exposure's initial margin exceeds
  the free balance. Strategies see it in `on_order_denied`.
- **What counts as new exposure.** A reduce-only order (`close_position`,
  `close_all_positions`) needs none. A plain order needs margin on its full quantity, less
  whatever it closes of the position it targets (`position_id`). That closing credit is the
  position's size less what working opposite orders on it already claim, so it is spent
  once whether the closes arrive in one order list or as separate submits; fills and
  cancels release it. The venue is HEDGING, so an
  untargeted opposite order opens a second position and is margined like one. In a bracket,
  only the entry is checked; children have a parent.
- **Modifies.** A `modify_order` may lower a working futures order's quantity or change
  its price, but never raise its quantity: any size increase is rejected
  (`OrderModifyRejected`, reason `FUTURES_QTY_INCREASE_NOT_SUPPORTED`), whatever the
  margin, and for entries, children and reduce-only orders alike. To trade a larger size,
  cancel the order, **wait for its `OrderCanceled`** (in `on_order_canceled`), then submit
  the new one. Until the cancel is applied the original order's margin stays reserved, so a
  replacement sent straight after `cancel_order` can be denied even when the account will
  have the funds. The new order gets the normal margin check. This keeps the gate
  stateless: it never has to hold margin for a change the venue has not applied yet. A
  decrease releases margin only once Nautilus applies it.
- **One quantity modify at a time.** A modify that sets a quantity is rejected (reason
  `FUTURES_MODIFY_PENDING`) while an earlier modify of the same order has not been applied
  (`OrderUpdated`) or rejected (`OrderModifyRejected`) yet; wait for it, then modify again.
  Each check compares against the order's current quantity, which a queued modify has not
  changed yet: 3 -> 1 and then 3 -> 2, sent back to back, would otherwise end at 2 after 1,
  an unchecked increase. Price-only modifies may still follow, because the venue applies
  them to whatever the quantity is by then. The gate counts outstanding modifies from the
  order's own events in the cache. Nautilus' `PENDING_UPDATE` status alone can't tell,
  because `Strategy.modify_order` sets it before sending and the first resolution clears
  it.
- **Free balance** is the venue account's realised cash minus locked margin (initial for
  working orders, maintenance for positions), minus the initial margin of every futures
  order on that venue that is submitted but not yet accepted (each at its own contract's
  rate). Orders pending update or cancel are already reserved and are not counted twice.
- **Limits.** Unrealised P&L is not counted (Nautilus leaves it out of the balance). There
  are no margin calls: maintenance margin is locked, never used to liquidate a losing
  position. Intraday (reduced) margins are not modelled; the spec holds overnight levels.

## Prices and ticks

The data is FirstRate continuous, ratio-adjusted. Ratio adjustment moves historical prices
off the exchange tick grid (ES shows `5922.58`), so the instrument's price increment is
`10^-continuous_price_precision`, not the exchange tick. Fills and stops are **not** rounded
to the real tick: rounding an adjusted price to a tick defined in unadjusted points would
bias every older fill by a different amount, and the adjusted series has no "real" tick to
round to. The real tick is kept on the instrument (`info["tick_size"]`) for live trading
(M5), where prices are unadjusted.

## Sizing (in-repo strategies and external ones)

Use the helpers in `src/core/position_sizing.py`; they are framework-free and handle
equities unchanged (multiplier 1):

```python
from src.core.position_sizing import (
    contract_notional, instrument_multiplier, units_for_notional, whole_contracts,
)

instrument = self.cache.instrument(self.instrument_id)
multiplier = instrument_multiplier(instrument)            # 50 for ES, 1 for a stock
units = units_for_notional(budget_usd, price, multiplier)  # budget / (price x multiplier)
contracts = whole_contracts(units)                         # floor; 0 -> skip the trade
```

- Risk-based sizing: `PositionSizingLogic().calculate_risk_based_size(balance, risk_pct,
  entry, stop, multiplier)` prices one unit's risk as `|entry - stop| x multiplier`; floor
  the result with `whole_contracts` for futures.
- `$100k` of ES at 7000 is 0.29 contracts (`$350k` each), which floors to **0**: no order.
  Never round a futures size up to 1. The equity minimum-of-one-share rule would more than
  triple the intended exposure.
- `sma_crossover` and the `buy_and_hold` benchmark already do this.
- **External strategies** (`src/core/strategies/custom/`, a git submodule, so not changed
  here) divide by price alone, e.g. `riptide.py` and `bollinger_reversal.py`. On ES they
  would ask for roughly 50x the intended exposure; the margin gate denies most such orders,
  so the run trades rarely or never. Port them to the helpers above in the submodule's own
  repo.

## Returns and what a run stores

- `trades.profit_loss` is Nautilus' realised P&L in dollars: `points x multiplier x
  contracts - commissions`.
- `trades.profit_pct` stays `(exit - entry) / entry x 100`: the return **on the underlying
  price**, the same formula as equities. It is not a return on margin. It also ignores the
  side, so a winning short shows a negative percentage, exactly as for equities. Ratio
  adjustment preserves percentage moves, so this figure is right for old trades even where
  dollar P&L is scaled.
- A futures run's `config_snapshot` records what M4's dashboard needs, frozen at run time:

  ```json
  "futures_contracts": {"ES.CME": {"multiplier": "50", "margin_initial_usd": "27500",
     "margin_maintenance_usd": "25000", "commission_per_contract_usd": "2.25",
     "margin_as_of": "2026-11-21"}},
  "profit_pct_basis": "underlying"
  ```

  From it and a trade row: notional = `quantity x entry_price x multiplier`, margin used =
  `quantity x margin_initial_usd`, return on margin = `profit_loss / margin used`. Equity
  runs carry neither key.
- Ratio adjustment scales old prices, so it scales old dollar P&L **and old points** alike.
  CL in 2008 trades at about 3.7x its real price, so both are about 3.7x too.
  `profit_loss_before_fees / (quantity x multiplier)` gives *adjusted* points, not points
  that were really traded. Compare old trades by percentage return; recovering real points
  needs the adjustment factor or unadjusted prices.

## Fixed contracts: `--contracts N`

`backtest run --contracts N` (N >= 1) trades exactly N whole contracts per entry, ignoring
the notional budget. It sets `SMAConfig.contracts`; a config file can set `contracts:` in its
`config:` section, and the flag overrides it. The flag is a usage error (exit 2) for a strategy
whose config has no `contracts` setting and for a symbol that is not a futures root. If the
loaded instrument turns out not to be futures, or a config file's value is not an int >= 1,
the run fails before the engine starts. Margin is still checked by `FuturesMarginGate`: 3 ES
on a $50k account are denied.

Note the CLI-mode default: without `--contracts`, `position_size_pct` 10% applies to a
`portfolio_value` of 10 x `--trade-size` (default $10M, not the starting balance), so ES
sizes to about 3 contracts. Config files, the web form and the MCP default to $1M, which
floors ES to 0.

## Reporting (M4): return on margin first

`src/services/futures/returns.py` computes everything below from the stored
`config_snapshot.futures_contracts` and the trade rows. It is pure, and every surface calls
it, so the figures can't drift. Equity runs get `None` and render as before.

| Figure | Formula |
|---|---|
| contracts | `quantity` |
| notional | `quantity x entry_price x multiplier` |
| margin used | `quantity x margin_initial_usd` |
| points (adjusted) | `exit - entry` long, `entry - exit` short |
| return on the underlying (per trade) | `points / entry x 100`, side-aware (unlike stored `profit_pct`) |
| return on margin (per trade) | `profit_loss / margin used x 100` (net of commission) |
| **return on margin (run, headline)** | `sum(closed P&L) / peak concurrent initial margin x 100` |
| return on the underlying (run) | per-trade returns compounded in exit order |

Peak concurrent margin is `max(contracts) x initial margin` for a one-position strategy. For
overlapping positions it is the margin tied up at once (exits count before entries on the
same timestamp).

Where it shows:

- `backtest show <run_id>`: a "Futures" table after the metadata, with the
  ratio-adjustment note.
- `history`: `ES (fut)` and `RoM +56.07%` in the Return column. Sorting by return still
  uses `total_return`.
- `report summary|generate|export-all`: text gets a section, CSV gets `asset_class` and
  `futures_*` rows, JSON gets `futures` (run fields, the note, per-trade figures).
- MCP `get_run`/`compare_runs`: `asset_class`, plus `futures` and a warning for futures
  runs; `compare_runs` warns when asset classes mix. `export_results`: `futures` per run,
  and futures trade columns when any exported run is futures.
- **Web dashboard: not yet.** Its routes and templates are under `FROZEN_VIEWS`. The edits
  it needs are listed in `openspec/changes/add-futures-reporting/design.md`, pending a PO
  ruling.

Runs made before M3 stored `futures_contracts` (for example the first ES smoke run) have no
terms, so they report as equity.
