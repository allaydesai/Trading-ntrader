# Design: futures margin, commission and sizing (M3)

## Where the terms come from

M1's builder copies each spec's `margin.initial_usd`, `margin.maintenance_usd`,
`margin.as_of` and `commission_per_contract_usd` into `FuturesContract.info`. `futures_terms`
reads them from the instrument, not from the YAML, so the instrument is self-describing.
That also covers specific contracts (M5), which can carry their own terms. A
`FuturesContract` without them raises: a futures run is never silently free or unmargined.

## Margin: a custom model, not a reference price

Nautilus' default (`LeveragedMarginModel`) is `notional / leverage x instrument.margin_init`,
a fraction of notional. Converting dollar margins to a fraction at a per-run reference price
would drift as the price moves: ES from 4000 to 7000 changes a fixed $27.5k into $16k or
$48k. Nautilus 1.220 exposes `MarginModel` as a `cdef` base class with `cpdef` methods and
`BacktestEngine.add_venue(margin_model=...)`. A Python subclass overrides it cleanly; the
margin account dispatches to the Python methods (a component test proves this), so
`FuturesMarginModel` returns `contracts x dollars` directly and nothing drifts. Instruments
without terms fall back to `LeveragedMarginModel`, Nautilus' own default.

## Enforcement: wrap the risk engine's endpoint

Nautilus 1.220 does not refuse over-margin orders on a margin account. `RiskEngine.
_check_orders_risk` returns `True` for margin accounts (a TODO in the source), and
`MarginAccount._recalculate_balance` clamps free balance at 0. Options considered:

1. **Subclass `RiskEngine`.** The kernel constructs it and offers no injection point.
2. **Wrap each strategy's `submit_order`.** This misses `submit_order_list`, the emulator and
   exec algorithms, and every external strategy.
3. **Wrap the `RiskEngine.execute` endpoint.** Chosen. All of the paths above send to it.

`MessageBus.deregister`/`register` are public; the gate re-registers in its own place and
calls the real `RiskEngine.execute` for everything it lets through. Denial goes through the
risk engine's own `cpdef _deny_order`/`_deny_order_list`, so the log line, the `OrderDenied`
event and the strategy's `on_order_denied` are exactly Nautilus'. The gate is installed only
when the run has a futures contract. `BacktestEngine` is single-use, so the gate lives
exactly as long as the run.

**Rules** (review round 1 added the list credit, venue-wide pending margin, the
in-flight state filter and modifies). Required = new-exposure contracts x initial margin. New exposure is 0 for
reduce-only orders and the full quantity otherwise, less what the order closes of its
targeted position (`position_id`), since the venue is HEDGING. In a list, only orders without
a parent are counted. The targeted position's closing credit is its size less what
working opposite orders on that position already claim (review round 2), so it is spent
once across a list and across separate submits. Free = the venue account's `balance_free(USD)` minus the initial margin of every
futures order on that venue in state `SUBMITTED` (not yet accepted, so not yet locked; each
at its own contract's rate). `PENDING_UPDATE`/`PENDING_CANCEL` are in flight too but already
reserved, so they are not subtracted again. The order is denied when required > free.

**Modifies** (simplified after review round 4, approved by the PO). A `ModifyOrder` that
would raise a futures order's quantity is always rejected through `_reject_modify_order`,
reason `FUTURES_QTY_INCREASE_NOT_SUPPORTED: ... cancel and resubmit instead`, whatever the
margin. Decreases and price-only modifies are forwarded unchanged. Rounds 2 to 4 tried to
allow margined increases by holding margin for approved but unapplied raises, matched to the
order's later `OrderUpdated`/`OrderModifyRejected` events. Nautilus events carry no command
id, so that matching could only go by arrival order, and each round turned up another
mismatch: a smaller second raise shrinking a larger hold, trailing-stop updates releasing
holds, a reentrant rejection being resolved twice (`IndexError`), and pending reductions
releasing margin early. Refusing increases removes the pending state entirely. The gate
keeps no per-order bookkeeping, and every check reads only orders' actual current
quantities and open positions from the cache. A decrease's margin is released only when
Nautilus applies it, so a pending reduction can never free margin early. Cancel and
resubmit gives a larger size the same check as any new order.

**Known limits** (documented):
- unrealised P&L is not counted;
- there are no margin calls or liquidation;
- overnight margins only.

## Commission

`FuturesCommissionModel` is `fill_qty x rate x scale`, per fill, so per side. `scale` is the
research cost-stress multiplier (it scales the equity model too). Instruments without a rate
use the equity fallback, so a mixed venue still charges equities per share.
`venue_cost_models` returns the unchanged `IBKRCommissionModel` and no margin model when a
venue has no futures contract, so equity runs make exactly the old `add_venue` call.

## Sizing

`units = budget / (price x multiplier)`. A multiplier other than 1 means a contract: floor
to whole contracts with no minimum, and skip the trade when the floor is 0. Rounding $100k of
ES up to one $350k contract would more than triple the intended exposure. Shares
(multiplier 1) keep the minimum of one share, and crypto keeps its fractional precision.
`instrument_multiplier` defaults to 1 for instruments without one (test doubles,
`SimpleNamespace`).

## Ticks and fills

The price increment stays `10^-continuous_price_precision` (M1), and fills and stops are not
rounded to the exchange tick. Ratio adjustment moves every historical price off the tick
grid by a factor that varies with date, so there is no meaningful tick to round an adjusted
price to, and rounding would add a date-dependent bias. The real tick stays in
`info["tick_size"]` for live trading (M5).

## Returns and stored fields

`profit_pct` keeps the equity formula, `(exit - entry) / entry`: a return on the underlying.
Ratio adjustment preserves percentage moves, so old trades are comparable. Return on margin
needs the margin used, so the run's `config_snapshot` freezes each contract's multiplier,
dollar margins and commission (`futures_contracts`), plus `profit_pct_basis`. M4 derives
notional, margin used, return on margin and points from a trade row and those values; no
migration is needed.
