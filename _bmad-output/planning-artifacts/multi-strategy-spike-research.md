---
title: Multi-strategy engine spike — research input for the phase 5b PRD
date: 2026-10-21
status: complete
probe: scripts/diagnostics/multi_strategy_spike.py
nautilus: 1.220.0
---

# Multi-strategy engine spike

Two registered strategies on one `BacktestEngine` with one account, four scenarios, one
process each. The probe is `scripts/diagnostics/multi_strategy_spike.py`
(`uv run python -m scripts.diagnostics.multi_strategy_spike --scenario all`). Every number
below is from the JSON it wrote on 2026-10-21; nothing is inferred from reading code alone
unless marked.

## Setup

| item | value |
|---|---|
| data | named catalog `e2e-test`, `1-DAY-LAST`, 2018-01-01 → 2019-12-31 (502 bars per symbol), FirstRate split-adjusted prices (AAPL first close 43.06) |
| account | one venue (`NASDAQ`), one USD account, starting balance 20,000 |
| strategy A | `sma_crossover`, tag `A`, `portfolio_value=20000`, `position_size_pct=100` — wants the whole balance on every entry |
| strategy B | `momentum`, tag `B`, `trade_size` = floor(0.6 × 20000 / first close) = 278 shares AAPL (139 MSFT) — ~60% of the balance |
| fill / fee | `make_fill_model(42)`, `IBKRCommissionModel` from settings, as the orchestrator builds them |
| risk | Nautilus default `RiskEngineConfig` (not bypassed), as every project backtest runs |

Combined demand on an entry day is ~160% of cash by construction.

## Scenario results

| | S1 HEDGING + MARGIN (today's venue) | S2 HEDGING + CASH | S3 NETTING + MARGIN | S4 HEDGING + CASH, A on AAPL / B on MSFT |
|---|---|---|---|---|
| run completed | yes | **no — engine stopped itself on 2018-02-22** | yes | **no — stopped 2018-02-26** |
| orders | A 15 filled, B 15 filled | A 1 filled, B 1 filled | A 15 filled, B 15 filled | A 1 filled, B 1 filled |
| orders denied | 0 | 0 | 0 | 0 |
| `RiskEngine` "Cannot check MARKET order risk: no prices" | 0 | 2 | 0 | 2 |
| positions report rows | 23 (A 8, B 15) | 1 (A) | 16 (A 8, B 8; 14 snapshots) | 1 (A) |
| rows without `strategy_id` | 0 | 0 | 0 | 0 |
| opener's strategy ≠ position's strategy | 0 | 0 | 0 | 0 |
| position sides | A: 7 FLAT + 1 LONG; **B: 8 LONG + 7 SHORT, all open** | A: 1 LONG | A: 7 FLAT + 1 LONG; B: 7 FLAT + 1 LONG | A: 1 LONG |
| account final (USD) | 25,134.45 | 30.80 (then −11,959.34 on B's fill) | 28,037.43 | 30.80 (then −13,233.58) |
| min free balance seen | 18,288.12 | 30.80 | 17,262.99 | 30.80 |
| A realized P&L (legs) | 5,155.30 | — | 5,155.30 | — |
| B realized P&L (legs) | −20.85 (open positions' commissions only) | — | 2,882.13 | — |
| A + B vs account delta | 5,134.45 = 5,155.30 − 20.85 ✓ | — | 8,037.43 = 5,155.30 + 2,882.13 ✓ | — |
| per-strategy Sharpe (realized returns, analyzer on that strategy's legs) | A 0.115, B nan | — | A 0.115, B 0.115 | — |
| combined Sharpe (mark-to-market daily equity, `ResultsExtractor`) | 1.47 | — | 1.47 | — |

## Answers

### Q1 — How does each strategy size its trades under shared capital?

**Fact:** neither built-in reads the account. A sizes `portfolio_value × pct / close` from a
fixed config number; B trades a fixed share count. Under today's MARGIN venue both got every
fill they asked for: in S1, B accumulated 15 open positions on a 20,000 account (8 longs and
7 shorts, see Q3's surprise), and A's 521-share entries used the whole balance while B's
278-share entries were filled beside them. The account never objected, because a MARGIN
account with no `margin_init` on the instrument locks nothing.

**Consequence for 5b:** "shared capital" is not something the engine imposes on these
strategies; it has to be given to them. A sleeve needs a capital slice as an input, and the
strategy must size from it. `sma_crossover` already has the right shape (`portfolio_value`
is exactly a slice); `momentum` has only `trade_size` in shares and cannot be given a slice
without a change. The spec's "sized from the slice of capital it's given" means a sizing
contract on the strategy side (a `capital` parameter every sleeve-eligible strategy honours),
not an engine feature. A fixed-share strategy on a slice is the same trap the 5a exit check
hit (crsi trading 100 shares on $1M behaved like cash).

### Q2 — Where can exposure limits be enforced?

**Fact:** Nautilus's `RiskEngine` cannot do it on bar data.

- MARGIN (today's venue): the engine returns before any balance or notional check
  (`risk/engine.pyx:652`). S1 and S3 show 0 warnings and 0 denials while 160% of cash was
  committed.
- CASH: the pre-trade check needs a quote or trade tick for a MARKET order. With bars only it
  logs `Cannot check MARKET order risk: no prices for AAPL.NASDAQ` and submits anyway (2 such
  warnings in S2, 2 in S4, 0 denials). The fill then drives the cash account negative,
  `CashAccount` raises `AccountBalanceNegative`, and **`BacktestEngine` stops the whole run**
  (`Stopping backtest from AccountBalanceNegative(balance=-11959.34, currency=USD)`): S2
  ended on the first day both strategies entered, with B's order FILLED but no position
  recorded for it. `engine.run()` returned normally; the stop is only visible in the log and
  in the truncated date range. A CASH venue therefore turns over-commitment into a silent
  partial run, not a limit.

**Consequence for 5b:** the three options from the question resolve as:

| option | verdict |
|---|---|
| Nautilus `RiskEngine` | not viable on bars without also publishing a synthetic quote per bar (possible, but doubles the data stream and only gives a per-order free-balance check, not per-sleeve exposure) |
| a wrapper around the strategies (pre-submit check of sleeve exposure against its slice and the portfolio's limits) | viable; the only place that knows the sleeve's slice. Needs an order-path hook — the project already has one pattern for intercepting strategy orders (`live_catch_up_gate.py` withholds orders) |
| a post-run check | viable as a *report* (the account report and the per-strategy fills give exposure over time), but cannot stop an over-committed fill, so on MARGIN the run's figures are of a portfolio that borrowed freely |

Keep the portfolio venue MARGIN (so the run completes) and enforce limits in the wrapper;
report realised exposure post-run as evidence. A CASH venue is not an option for the
portfolio backtest.

### Q3 — How can results be stored and attributed?

**Fact:** the engine's reports split cleanly by `strategy_id` in every scenario, including
NETTING: 0 unattributed rows, 0 positions whose opening order belonged to another strategy.
Under NETTING closed legs are snapshots (14 of 16 rows in S3), and the snapshots carry
`strategy_id` too. Position ids in a HEDGING backtest are venue-generated
(`NASDAQ-1-001`, …) and do not contain the strategy; the column does.

Per-strategy figures, measured:

| figure | how | works? |
|---|---|---|
| closed trades, realized P&L, commissions | `cache.positions_closed(strategy_id=…)` | HEDGING only — under NETTING `positions_closed` is empty (legs are snapshots) |
| the same, OMS-independent | `[*cache.positions(), *cache.position_snapshots()]` filtered on `strategy_id`, the `LiveHost.settle` pattern | yes: A's figures are identical in S1 and S3 (5,155.30; Sharpe 0.1149) |
| per-strategy analyzer statistics | `copy.deepcopy(engine.portfolio.analyzer).calculate_statistics(_Ledger.over(legs, capital), legs)` | yes, including "PnL (total)" once the ledger stands in for the account |
| per-strategy sums reconcile to the account | Σ realized P&L over legs = account delta | to the cent in S3; in S1 the −22.73 gap is the entry commissions of positions still open |
| per-strategy mark-to-market equity / Sharpe | `mark_to_market.daily_equity(capital, fills, closes)` with that strategy's fills | not run in the spike, but the function is pure pandas and takes any fill list; the capital base is the sleeve's slice, which is a design input (Q1), not a measurement |
| account report per strategy | `generate_account_report` | no — account-wide only |

So one run can carry per-strategy figures: trades already have `strategy_id` (step 1); the
run row needs a per-sleeve block (closed trades, realized P&L, commissions, realized-return
stats, and a mark-to-market curve on the sleeve's slice). A parent run with a child run per
sleeve is not forced by the engine; it would be a presentation choice. The two Sharpes are
by design (realized-return per sleeve vs mark-to-market combined), the same split the DB
already has for single runs.

## Surprises

1. **`momentum` never closes a position on the project's HEDGING venue.** It exits with a bare
   `order_factory.market(SELL, qty)`; under HEDGING that opens a new short position instead
   of closing the long (S1: 8 LONG + 7 SHORT, all open, 0 closed; S3 under NETTING: 7 FLAT +
   1 LONG, 2,882.13 realized). `sma_crossover` closes through `close_position(position)` and
   is fine under both. Every single-strategy `momentum` backtest the project has run on the
   HEDGING venue has therefore reported 0 closed trades and all P&L as open. Pre-existing,
   outside 5b's scope, but a sleeve-eligibility rule follows: a sleeve must close by position
   (or the portfolio venue must be NETTING, which the extractor already handles).
2. **`engine.run()` does not raise when the engine stops itself** on `AccountBalanceNegative`;
   the run just ends early. Any 5b job must assert the run reached the requested end date.
3. **A CASH account's over-committed fill is recorded as FILLED with no position** (S2/S4: B's
   order FILLED, positions report has only A's row).
4. The first bar of a run logs `Received <Bar[0]> data for unknown bar type` once per strategy.
   Harmless, present in single-strategy runs too.

## Open items for the PRD

- Define the sizing contract (Q1): the parameter name, which strategies honour it, and what
  happens to a sleeve that does not (refuse at study creation, as the 5a quiet-sleeve warning
  suggests, or run and warn).
- Define the exposure wrapper (Q2): per-sleeve cap on its slice, portfolio-level gross/net
  caps, instrument-overlap rule, and whether a withheld order is recorded like
  `bars.catch_up_order_withheld`.
- Choose the per-sleeve storage shape (Q3) and the sleeve's capital base for its own curve.
- Decide whether the portfolio venue is HEDGING (today, needs position-aware exits) or
  NETTING (matches live, extractor already copes).
