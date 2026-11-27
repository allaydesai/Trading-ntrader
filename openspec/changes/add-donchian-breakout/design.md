# Design

## Context

See proposal.md (Why) and `specs/donchian-breakout-strategy/spec.md` for the rules.

- Strategies live in the private submodule `src/core/strategies/custom/` (repo
  `Trading-ntrader-strategies`, pinned on main at `028db77`). The registry scans `custom/` and
  imports each module; a module registers itself with `@register_strategy` plus the three
  `StrategyRegistry.set_*` calls. Parameter models live in the submodule's `models.py`.
- `_sizing.risk_quantity` already sizes a stop-distance risk budget for equities (whole
  shares, at least 1), futures (whole contracts, may be 0), spot FX (base units priced in USD,
  crosses need `fx_quote_rates`) and spot crypto (fractional coins, capped at free USD cash,
  0 under Kraken's minimum). `bollinger_reversal` is the model to copy for wiring it,
  including `_free_cash` and the crypto short skip (`_crypto.skip_short`).
- The research server builds every run's `BacktestRequest` with the default fill model
  (`fill_model` is only settable from a CLI YAML). Under it a market order sent from
  `on_bar(T)` fills at bar T's close, and a resting stop-market order that bar T+1 gaps through
  fills at its trigger, not at the open.
- Data (daily bars): stocks and ETFs from 2000 (EEM 2003, GLD 2004, TLT 2002), futures from
  2008, FX from 2010-01-03, BTC from 2013-03-31, ETH from 2016-03-10, SOL from 2021-02-28.
  Every series ends 2026-05-01 (FX 2026-04-30).
- The vault's `System/Gates.md` G0 prose says daily signals act on the next bar. The scorecard
  does not check that rule automatically.

## Goals / Non-Goals

**Goals:**
- One strategy that runs unchanged on all five asset classes, with fills that are consistent
  with "act at the close of the signal day".
- Every study judged by the same gates, the same windows where data allows, and the same
  parameter grid, so the cross-market scorecard compares like with like.

**Non-Goals:**
- Any platform change (fill model, research server, registry, sizing helpers).
- Optimising each market separately beyond the shared grid.

## Decisions

### D1. Approaches considered (Allay picked 1, 2026-11-26)

1. **Donchian breakout with an ATR stop (Turtle-style)**: chosen. It is the rule set in the
   crypto Donchian paper and what the trend-following funds behind the futures evidence
   trade; a daily signal gives enough trades to judge; three tunable parameters fit a study
   budget; sizing reuses `risk_quantity` unchanged.
2. **Time-series momentum** (monthly sign of the 12-month return, volatility-targeted):
   closest to Moskowitz et al., but about 12 decisions a year (too few trades for G0 on BTC's
   history) and it needs volatility-targeted rebalancing logic that does not exist.
3. **Moving-average crossover**: dropped; it duplicates `sma_crossover`.

### D2. Shorts: an `allow_short` setting (Allay picked B, 2026-11-26)

On for the futures and FX studies, off for stocks, ETFs and crypto. Alternatives: long-only
everywhere (makes "long EURUSD" an arbitrary directional bet and drops the down-trend side the
futures evidence relies on) and long/short everywhere except crypto (shorting single stocks
and SPY usually loses in trend tests). Crypto ignores the setting with a one-time log, because
the spot account denies a net short (`CRYPTO_SPOT_NO_SHORT`).

### D3. Execution at the signal bar's close (Allay ruling, 2026-11-26)

Every order is a market order sent from `on_bar` on the signal bar, filling at that bar's
close, as a proxy for entering a few minutes before the close. Waiting for the next open gives
away the overnight gap in the breakout's direction. The G0 "next bar" wording is waived for
this strategy by that ruling; each study's hypothesis records it. A more accurate pre-close
intraday entry is a follow-up (proposal, Out of scope). Alternative considered: making the
research server honour the gap-aware fill model (next-bar execution); not wanted.

### D4. Stops are checked on the close, not resting orders

The stop level is fixed at entry (`close - stop_atr_mult x ATR` for a long) and checked
against each bar's close; when breached, the position is closed at market on that bar. Why:
under the default fill model a resting stop that the next bar gaps through fills at the
trigger, which flatters losses; a close check keeps all fills at a real close, matches D3, and
needs no bracket bookkeeping (and no crypto pending-cancel claims). Trade-off: an intrabar
move through the stop that recovers by the close is not stopped out, and a close well beyond
the stop loses more than `max_risk_pct`. That is the honest cost of a close-only model.

### D5. Channels exclude the signal bar

The entry and exit channels are the highest high / lowest low of the N (or M) bars *before*
the current one, kept in a fixed-length deque of highs and lows (not Nautilus'
`DonchianChannel`, which includes the current bar). ATR is Nautilus' `AverageTrueRange` with
`atr_period` (default 20), read on the entry bar and frozen into the stop.

### D6. Sizing: `risk_quantity`, then a notional cap

`portfolio_value` (static, like the other submodule strategies; not compounding) x
`max_risk_pct` / stop distance through `risk_quantity`, then capped so `size x price x
multiplier` (USD-priced for FX) is at most `max_position_pct` of `portfolio_value` (default
100). The cap stops low-volatility instruments (ZN, EURGBP) from sizing past the account on a
tight ATR. Crypto is already capped by free cash inside `risk_quantity`. `contracts` / `units`
override both.

### D7. No reversal on an exit bar

A bar that closes a position does not also open one (spec). This keeps one order per bar and
avoids a same-bar close-and-reverse that the crypto account and the NETTING position model
would both have to reconcile. Since `exit_lookback < entry_lookback`, an opposite N-day
breakout always also breaks the M-day exit channel, so the reversal is taken at most one bar
late, on the next bar's re-check.

### D8. Defaults

`entry_lookback` 55, `exit_lookback` 20 (Turtle system 2), `atr_period` 20, `stop_atr_mult`
2, `max_risk_pct` 1, `max_position_pct` 100, `allow_short` false, `portfolio_value`
1,000,000.

### D9. Study design (research, no code)

| Market | Catalog | Lead | Breadth basket | `allow_short` | In-sample | Out-of-sample |
|---|---|---|---|---|---|---|
| Stock | `firstrate-stocks` | AAPL | MSFT, NVDA, JPM, XOM | off | 2010-01-01 to 2021-12-31 | 2022-01-01 to 2026-04-30 |
| ETF | `firstrate-etf` | SPY | QQQ, TLT, GLD, EEM | off | same | same |
| Futures | `firstrate-futures` | CL | ES, NQ, GC, ZN | on | same | same |
| FX | `firstrate-fx` | EURUSD | GBPUSD, USDJPY, EURGBP | on | same | same |
| Crypto | `firstrate-crypto` | BTC | ETH, SOL | off (ignored) | 2014-01-01 to 2021-12-31 | same |

- One common in-sample start (2010, when FX data begins) so all five markets see the same
  regimes; crypto starts 2014 to give BTC a full warm-up after its 2013 data start. A 4.3-year
  out-of-sample window, because a 3-year one would give most markets under 30 trades.
- Shared grid, `allow_short` fixed per study: `entry_lookback` in {20, 55, 100},
  `exit_lookback` in {10, 20, 50} with exit < entry (6 pairs), `stop_atr_mult` in {2, 3, 4}:
  18 trials. Trial budget 30 per study, leaving room for the G2 neighbourhood sweep.
- Lead choice per study: the best in-sample trial by Sharpe among those with at least 30
  trades and profit factor >= 1.3; if none qualifies, the best Sharpe, noted as not meeting
  G1. Frozen with `freeze_candidate`, run out of sample once.
- After the freeze: `submit_cost_stress` (2x), `submit_sweep` neighbourhood +
  `run_sensitivity`, `run_monte_carlo` on the in-sample source run, `submit_breadth` on the
  basket for both windows, `submit_walk_forward`, `submit_benchmark` (buy-and-hold) on the
  lead for both windows, then `get_scorecard`. G4 (paper) stays missing.
- Basket view: `run_portfolio_backtest` with the lead's OOS run as the candidate and the
  breadth OOS runs as equal-weight sleeves.
- `kraken-crypto` cross-check (optional, per Allay's 2026-11-25 ruling that `firstrate-crypto`
  is primary and Kraken a sanity test): one
  unattributed BTC run of the frozen crypto parameters on `kraken-crypto`, reported beside
  the crypto row.
- Each study's hypothesis states D3 (close execution, G0 next-bar wording waived) and, for
  SPY, that its 2022+ holdout was already viewed by earlier pullback-family studies.
- Output: `export_results(study=...)` per study, and one cross-market scorecard note in the
  vault (`Experiments/`), with a row per market: gate verdicts G0-G3, lead IS/OOS Sharpe,
  PF, max drawdown, trades, buy-and-hold beside it, breadth PFs, and the basket portfolio's
  pooled trades and Sharpe. Statuses (rejected / parked / promoted) are left to Allay.

## Risks / Trade-offs

- [Trade counts under 30 on slow channels, especially OOS] → the grid includes 20/10 and the
  OOS window is 4.3 years; any market still under 30 is reported "inconclusive", not
  pass/fail.
- [Close-to-close fills are slightly optimistic: the signal uses the close it fills at] →
  Allay's ruling D3; the scorecard note states it on every row.
- [Close-checked stops can lose more than `max_risk_pct` on a gap] → stated in D4; the
  Monte Carlo drawdown check (G2) captures the tail.
- [Crypto breadth has only 2 instruments; G2 breadth needs 3] → reported as a fail with that
  reason; adding coin specs is out of scope.
- [SOL has almost no in-sample data] → its in-sample breadth run is reported but not judged.
- [SPY's holdout was seen before] → noted on the SPY row; QQQ in its basket gives a fresh view.
- [The research server runs one job at a time; ~5 x 40 runs] → batch submissions per study
  and run the five studies sequentially; daily bars keep each run short.
- [The strategy file in the submodule must stay under the size caps] → one class with small
  helpers; the parameter model goes in `models.py`.

## Migration Plan

Strategy PR in `Trading-ntrader-strategies` first; after it merges, a NTrader PR bumps the
pin and adds `configs/donchian_breakout.yaml`. Rollback is reverting the pin bump. The
research runs only add rows.
