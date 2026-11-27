# Proposal: Donchian breakout strategy, studied across five markets

## Why

NTrader now backtests stocks, ETFs, futures, spot FX and spot crypto, but no strategy has been
put through the full study process in every market. Trend following is the strategy family
with the broadest published cross-market evidence (time-series momentum in 58 futures across
equity indices, currencies, commodities and bonds, Moskowitz, Ooi and Pedersen 2012; 67
markets back to 1877, Hurst, Ooi and Pedersen; a Donchian breakout on Bitcoin and altcoins,
Zarattini, Pagani and Barbon, results unverified). The repo has no breakout strategy yet: its
strategies are SMA crossover, momentum, RSI mean reversion, Bollinger reversal and Riptide.
This change answers two questions at once: does the multi-market setup work end to end through
the study process, and does one trend rule have an edge in any of these markets? An edge in
only some markets is an acceptable result.

## What Changes

- **New strategy `donchian_breakout`** (private strategies submodule,
  `src/core/strategies/custom/donchian_breakout.py`): a daily close-to-close Donchian channel
  breakout. Long on a close above the prior N-day high; short on a close below the prior N-day
  low only when `allow_short` is on; exit on a close through the prior M-day opposite channel
  or through a K x ATR stop fixed at entry. Every order is a market order sent on the signal
  bar, so it fills at that bar's close. Risk-based sizing through the existing
  `_sizing.risk_quantity` (whole shares, whole contracts, FX base units, fractional coins),
  capped at a notional share of the portfolio.
- **Parameter model** `DonchianBreakoutParameters` in the submodule's `models.py`, registered
  with `StrategyRegistry` (config, parameter model, default config).
- **Submodule tests** for the signal, exit, stop, short-gating and sizing rules, plus one
  engine test per asset class.
- **`configs/donchian_breakout.yaml`** in NTrader, and the submodule pin bumped to the merged
  strategy.
- **Research (no code):** five studies, one per market, each tuned on a lead asset, frozen,
  run once out of sample, then checked for breadth on the rest of the market's basket; an
  equal-weight portfolio view of each basket; and a cross-market scorecard note in the
  Trading Research vault.

## Capabilities

### New Capabilities

- `donchian-breakout-strategy`: the rules, sizing and per-asset-class behaviour of the
  `donchian_breakout` strategy.

### Modified Capabilities

None. No platform behaviour changes: the strategy uses the existing default fill model, sizing
helpers and research server tools as they are.

## Impact

- **Interfaces crossed:** the strategy registry gains one name, so `list_strategies`,
  `describe_strategy`, the CLI's strategy list and the web UI's strategy picker show
  `donchian_breakout`. No MCP tool contract, CLI flag, HTTP route, DB schema or migration
  changes.
- **Submodule pin:** `src/core/strategies/custom` moves from `028db77` to the merge of the
  strategy PR in `Trading-ntrader-strategies`.
- **Guard lists / pinned constants:** none touched. No `src/core/live_*.py` or
  `src/cli/commands/live*.py` module changes. `FROZEN_VIEWS` is unaffected (no `src/api/**`
  or `templates/**` edit). The size-cap guard applies to the new strategy file (must stay
  under 500 statement lines, functions under 50, the class under 100).
- **Data:** read-only use of `firstrate-stocks`, `firstrate-etf`, `firstrate-futures`,
  `firstrate-fx` and `firstrate-crypto` (the primary crypto catalog). `kraken-crypto` is an
  optional cross-check only.
- **Database:** about 5 x (up to 24 trials + benchmarks + 1 OOS run + breadth runs + cost
  stress) persisted backtest runs and research rows.

## Out of scope

- Multi-instrument basket runs in one engine (shared capital, true pooled statistics): this is
  the separate Phase 5b work. Baskets are judged per symbol plus a stored-curve portfolio.
- Entering a few minutes before the close on intraday bars (Allay, 2026-11-26: the backtest
  enters at the signal day's close for now; pre-close intraday entry is a follow-up).
- The gap-aware fill model and next-bar execution for research-server runs.
- Volatility-targeted rebalancing (the time-series momentum variant), pyramiding, and coin
  specs beyond BTC, ETH and SOL.
- Changing the vault's `System/Gates.md`.
- Paper trading the strategy.
