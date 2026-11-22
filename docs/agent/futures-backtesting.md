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
- **Modifies.** A `modify_order` that raises a working order's quantity (entry or
  non-reduce-only child) is rejected (`OrderModifyRejected`, same reason prefix) when the
  added contracts' initial margin exceeds free balance. Price-only, quantity-reducing and
  reduce-only modifies always pass. An approved increase is held against free balance until
  the exchange applies it, rejects it or the order closes.
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
  needs the adjustment factor or unadjusted prices (M4).
