# Design: next-bar-open exits, gap-aware stops, slippage drag

## Why a fill model alone cannot fill on the next bar

In Nautilus 1.220 the backtest loop for each bar T is: advance clocks → `exchange.process_bar(T)`
(matching against T's open, high, low, close) → `data_engine.process(T)` (the strategy's
`on_bar`) → `exchange.process(T)` (commands the strategy just sent). A market order sent from
`on_bar(T)` is therefore matched in `exchange.process(T)`, against T's close.
`FillModel.get_orderbook_for_fill_simulation` can only change the *price* of that fill:
returning an empty book does not defer the order, it rejects it (`apply_fills` rejects a
market order with no fills). #39 repriced to T+1's open while the fill, the position close and
the realised P&L stayed on T: one bar early, with a price from the future.

`TimeInForce.AT_THE_OPEN` market orders are logged as unsupported and dropped by the matching
engine, and the strategy lives in a pinned submodule, so the fix has to be at the venue.

## Options considered

1. **Remove the next-open repricing**: MOO exits fill at T's close. Simple and honest, but
   departs from the strategy's spec (sell at the next open) and overstates exit quality on
   gap days.
2. **Global latency only**: a venue `LatencyModel` delays every command, but commands then
   reach the venue in `exchange.process(T+1)`, *after* T+1's prices were matched: entry limits
   could no longer fill on T+1's dip and stops would rest a bar late.
3. **Latency + a pre-bar flush (chosen)**: a 1 ns `LatencyModel` keeps commands sent during T
   in flight past `exchange.process(T)`. `NextBarExecutionModule.pre_process(bar)` runs at the
   start of `exchange.process_bar(T+1)`, before the matching engine sees T+1, and calls
   `exchange.process(ts)`, which releases them. The book still holds T's close, nothing has
   traded since, so limit, stop and cancel commands behave exactly as if processed at the end
   of T. An MOO-tagged market exit is repriced by `GapAwareFillModel` to the open of the bar
   now being processed (T+1) and its fill, position close and P&L carry T+1's timestamp.

## Consequences (documented in the module)

- Only a venue whose fill model is a `GapAwareFillModel` gets the latency model and module;
  every other run is set up exactly as before (verified by identical smoke results).
- An untagged market order on a gap-aware venue fills at the prior close (the book has not
  moved) but is stamped on the next bar. Riptide tags every market order it sends.
- An MOO exit signalled on the final bar has no next bar and does not fill; the position is
  reported open, as for any order still working at the end of a run.
- A timer firing between two bars would also release in-flight commands; Riptide sets none.
- Nautilus processes every venue after each data point, so any bar in the engine releases
  in-flight commands, on any venue. Every instrument in a gap-aware run must therefore have
  bars at the same timestamps; `bind_fill_model` rejects a run whose series differ (review
  rounds 1 and 2). Bars are keyed by `ts_init`, the time the engine
  clock is set to.

## Futures and the slippage drag

`slippage_per_share` is a fixed cost per filled *share*, added on top of the IBKR capped
commission. On a futures venue `venue_cost_models` charges `FuturesCommissionModel`
(per-contract, per-side from the contract spec) and uses the equity model only as the fallback
for non-futures instruments on that venue. So the per-share drag never applies to a futures
contract: a cent per contract would be meaningless against ES's $12.50 tick, and silently
changing M3/M4 futures results is not wanted. Futures slippage, if wanted, should be a
per-contract tick count in the contract spec; out of scope here. A component test pins that a
futures fill's fee is unchanged when the equity model carries slippage.

`SLIPPAGE_PER_SHARE` defaults to 0, so equity results are unchanged unless it is set.
