# Apollo RSI Backtest Analysis — QQQ (2000-2025)

**Run ID:** `87737e96-dfd5-417a-88ad-acd8cfa0d32b`
**Verified:** `490e75dd-3868-459b-bcf8-4ae965b4483e` (2026-02-22, reproduced within rounding tolerance)
**Date:** 2026-02-21
**Config:** `configs/apolo_rsi_qqq.yaml`

## Strategy Summary

2-period RSI mean reversion, long-only. Buys when RSI(2) < 0.10 (oversold), sells when RSI(2) > 0.50 (mean reversion). Uses 50% of $100K portfolio per trade. Daily bars on QQQ.NASDAQ, 2000-01-03 to 2025-12-29.

## Configuration

| Parameter | Value |
|-----------|-------|
| RSI Period | 2 |
| Buy Threshold | 0.10 (RSI < 10) |
| Sell Threshold | 0.50 (RSI > 50) |
| Position Size | 50% of portfolio |
| Initial Capital | $100,000 |
| Data Source | Parquet Catalog |

## Full Metrics

### Returns & Risk

| Metric | Value |
|--------|-------|
| Total Return | 137.32% |
| CAGR | 3.38% |
| Final Balance | $237,318.63 |
| Commissions Paid | $9,080.86 |
| Max Drawdown | -31.68% |
| Volatility (annualized) | 7.97% |
| Sharpe Ratio (252 days) | 0.45 |
| Sortino Ratio (252 days) | 0.61 |
| Calmar Ratio | 0.11 |
| Risk Return Ratio | 0.12 |

### Trading Statistics

| Metric | Value |
|--------|-------|
| Total Trades | 1,258 |
| Winning Trades | 878 |
| Losing Trades | 380 |
| Win Rate | 69.8% |
| Profit Factor | 1.40 |
| Expectancy | $109.07/trade |
| Avg Winner | $576.57 |
| Avg Loser | -$968.26 |
| Max Winner | $4,338.40 |
| Max Loser | -$5,990.49 |
| Avg Win Return | 1.17% |
| Avg Loss Return | -1.94% |
| Trade Frequency | ~48 trades/year |

## Evaluation

### 1. Returns — Underperforms buy-and-hold

- Strategy CAGR of **3.38%** vs QQQ buy-and-hold CAGR of roughly **7-8%** over the same period (QQQ went from ~$88 to ~$520-530).
- $100K grew to $237K with the strategy vs ~$600K+ with buy-and-hold.
- The strategy only deploys 50% of capital per trade and is flat (uninvested) most of the time — massive **cash drag**.

### 2. Risk — Acceptable drawdown, poor risk-adjusted returns

- -31.68% max drawdown is meaningful but less than QQQ's -83% dot-com drawdown and -35% COVID drawdown.
- Sharpe of 0.45 is weak (target >1.0). Sortino of 0.61 slightly better due to positive skew.
- Calmar of 0.11 is poor — you're not being compensated for the drawdown risk.

### 3. Trade quality — High win rate, poor risk/reward

- 70% win rate is strong, but avg loser ($968) is **1.68x** avg winner ($577).
- Each loss wipes out ~1.7 wins. The edge is thin.
- Profit factor of 1.40 confirms marginal profitability.
- Max loser ($5,990) exceeding max winner ($4,338) shows unbounded downside risk.

### 4. Drawdown advantage

- While the strategy's -31.68% max drawdown is painful, it substantially protected capital vs QQQ buy-and-hold during the worst regimes: QQQ's -83% dot-com crash and -35% COVID drawdown.
- Being out of the market most of the time acts as an implicit hedge, but the cost is the massive opportunity cost during bull runs.
- At 7.97% annualized volatility, the strategy is roughly 3-4x less volatile than QQQ (~20-25% annualized). This is a real advantage — but Calmar of 0.11 shows the return doesn't justify even the reduced drawdown.

### 5. Consistency

- 1,258 trades over 26 years = ~48 trades/year (roughly one every 5 trading days) — good sample size and statistically meaningful.
- The 2-period RSI is very responsive, generating frequent signals.

### 6. Open position at backtest end

- The last action was a **BUY of 80 shares at $620.87** on 2025-12-29 (final bar). This position was never closed, so unrealized PnL of $0.00 was reported.
- Actual total return may be slightly higher or lower depending on where QQQ goes next. Minor effect on a 26-year backtest, but worth noting for methodology.

## Root Causes of Underperformance

1. **No trend filter** — Buying oversold dips in a sustained downtrend (2000-2002, 2008) gets destroyed. Mean reversion fails when the regime is trending.
2. **Fixed threshold position sizing** — 50% of *initial* $100K, never scales with equity growth. At $237K the strategy is still betting $50K per trade — the compounding engine is completely broken.
3. **Asymmetric exits** — Sell threshold of 0.50 (RSI at median) exits winners too early. Losers have no stop-loss and ride down until RSI happens to bounce above 50. This creates the 1.68x loser/winner asymmetry.
4. **Cash drag** — Uninvested capital earns 0% in this backtest. With ~48 trades/year and short holding periods, the strategy is flat for most of the year while QQQ compounds.
5. **No max loss protection** — Max loser ($5,990) exceeds max winner ($4,338) by 38%. A single catastrophic loss can wipe ~10 average wins ($577 each). Without a stop-loss, tail risk is unbounded.

## Refinement Suggestions

### High impact — config parameter changes

| Change | Current | Suggested | Rationale |
|--------|---------|-----------|-----------|
| Sell threshold | 0.50 | 0.65-0.70 | Let winners run longer; exit further into strength |
| Buy threshold | 0.10 | 0.05 | More extreme oversold = higher probability of bounce |
| Position sizing | Fixed 50% of $100K | % of current equity | Scale with portfolio growth |

### Medium impact — strategy code changes

1. **200-day SMA trend filter** — Only take buy signals when price > SMA(200). This single filter would have avoided most of the dot-com and 2008 drawdowns.
2. **Time-based stop** — Exit after N days (e.g., 5-10) if RSI hasn't crossed sell threshold. Prevents holding through extended declines.
3. **Max loss stop** — Exit if position drops more than 5-8% from entry. Caps the worst losers.

### Lower impact — tuning

4. **RSI period 3 instead of 2** — Slightly smoother signal, fewer whipsaws.
5. **Multiple entries** — Average into oversold positions (buy more if RSI < 0.05 after initial buy at < 0.10).

### Validation — avoid overfitting

6. **Walk-forward analysis** — Split into in-sample (2000-2015) and out-of-sample (2016-2025) to check if the edge degrades over time. The post-2020 QQQ regime (high vol, meme-era) is structurally different from 2000s.
7. **Multi-asset test** — Run the same parameters on SPY, DIA, IWM, and sector ETFs (XLK, XLF). If the edge generalizes, the strategy is robust. If it only works on QQQ, it may be curve-fitted to NASDAQ volatility patterns.
8. **Parameter sensitivity sweep** — Test buy threshold [0.05, 0.10, 0.15, 0.20] x sell threshold [0.50, 0.60, 0.70, 0.80] to map the profit surface. A strategy that only works on a narrow parameter island is fragile.

## Bottom Line

The Apollo RSI strategy has a **genuine statistical edge** — 69.8% win rate and positive expectancy over 1,258 trades across 26 years is not noise. However, it **massively underperforms buy-and-hold QQQ** ($237K vs ~$600K+) due to cash drag and broken compounding. The 3.38% CAGR barely outpaces inflation and trails even a simple bond allocation.

**The core thesis (2-period RSI mean reversion) is sound but the execution needs work.** The highest-leverage fixes are:

1. **Trend filter (SMA 200)** — avoid buying dips in bear markets
2. **Equity-based position sizing** — compound gains instead of flat-betting
3. **Stop-loss (5-8%)** — cap tail risk on losers

With these three changes, this strategy could potentially compete with or complement a buy-and-hold allocation by offering lower drawdowns with acceptable returns. Without them, it's a capital-inefficient use of $100K.

---

## Refinement Run #1 — Combined: SMA 200 + Equity Sizing + 5% Stop-Loss

**Run ID:** `c6f3c8b4-a87d-4da8-a60a-1bc34fc932d8`
**Date:** 2026-02-23
**Config changes:** `sma_trend_period: 200`, `use_equity_sizing: true`, `stop_loss_pct: 0.05`

### Comparison: Baseline vs Refined

| Metric | Baseline | Refined | Delta |
|--------|----------|---------|-------|
| Total Return | 137.32% | 68.65% | **-68.67 pp** |
| CAGR | 3.38% | 2.03% | -1.35 pp |
| Final Balance | $237,319 | $168,645 | -$68,673 |
| Sharpe Ratio | 0.45 | 0.30 | -0.15 |
| Sortino Ratio | 0.61 | 0.38 | -0.23 |
| Profit Factor | 1.40 | 1.31 | -0.09 |
| Volatility | 7.97% | 4.87% | -3.10 pp |
| Total Trades | 1,258 | 809 | -449 (-36%) |
| Win Rate | 69.8% | 71.8% | +2.0 pp |
| Avg Winner | $576.57 | $554.09 | -$22.48 |
| Avg Loser | -$968.26 | -$1,106.03 | -$137.77 (worse) |
| Max Loser | -$5,990 | -$5,167 | +$823 (better) |
| Expectancy | $109.07 | $84.75 | -$24.32 |
| Commissions | $9,081 | $6,253 | -$2,828 |

### Analysis

**The combined refinement made the strategy worse, not better.** Total return dropped from 137% to 69%, CAGR fell from 3.38% to 2.03%.

**What improved:**
- Win rate ticked up 69.8% → 71.8% (SMA filter removes low-quality entries in downtrends)
- Volatility cut nearly in half: 7.97% → 4.87% (fewer trades, less market exposure)
- Max loser improved: -$5,990 → -$5,167 (stop-loss partially capped tail risk)
- Fewer trades (809 vs 1,258) means less commission drag ($6.3K vs $9.1K)

**What got worse:**
- Total return halved — the SMA filter removed ~449 trades, many of which were profitable. The 2-period RSI mean reversion signal works even in downtrends (short holding periods); the SMA filter over-filtered.
- Avg loser worsened: -$968 → -$1,106. The 5% stop-loss didn't reduce average losses — it may be triggering on intraday noise and then missing the subsequent rebound (the core edge is buying sharp dips that revert within 1-3 days).
- Equity-based sizing compounds losses in drawdowns (smaller positions after losses = slower recovery) while the baseline's fixed sizing maintained constant exposure.
- Sharpe dropped 0.45 → 0.30. The return reduction outweighed the volatility reduction.

### Root Cause Hypothesis

The three changes interact negatively for this specific strategy:

1. **SMA 200 + 2-period RSI conflict**: RSI(2) mean reversion has a 1-3 day holding period. It profits from buying sharp intraday panics that revert quickly — even in bear markets. The SMA filter blocks these high-probability trades during extended downtrends, where oversold bounces are actually the strongest.

2. **Stop-loss + mean reversion conflict**: A 5% stop-loss on a strategy that buys sharp dips will frequently trigger *during* the dip (before the reversion happens). The stop-loss is cutting the strategy off before its thesis can play out.

3. **Equity sizing + reduced opportunity set**: With fewer trades (SMA filter) and more losses being realized (stop-loss), the equity base shrinks more often, compounding the problem.

---

## Refinement Run #2 — Isolated Tests (One Change at a Time)

**Date:** 2026-02-23

### Full Comparison Table

| Metric | Baseline | SMA Only | Equity Only | StopLoss Only | All Three |
|--------|----------|----------|-------------|---------------|-----------|
| **Total Return** | 137.32% | 61.24% | **271.34%** | 144.08% | 68.65% |
| **CAGR** | 3.38% | 1.85% | **5.18%** | 3.49% | 2.03% |
| **Final Balance** | $237,319 | $161,240 | **$371,342** | $244,083 | $168,645 |
| Sharpe Ratio | 0.45 | 0.35 | **0.45** | 0.38 | 0.30 |
| Sortino Ratio | 0.61 | 0.45 | **0.61** | 0.52 | 0.38 |
| Profit Factor | 1.40 | 1.37 | **1.40** | 1.33 | 1.31 |
| Volatility | 7.97% | 4.67% | 7.97% | 10.00% | 4.87% |
| Total Trades | 1,258 | 805 | 1,258 | 1,331 | 809 |
| Win Rate | 69.8% | 71.8% | 69.7% | 69.9% | 71.8% |
| Expectancy | $109.07 | $75.98 | **$215.52** | $108.17 | $84.75 |
| Avg Winner | $576.57 | $420.74 | $1,077.23 | $654.41 | $554.09 |
| Avg Loser | -$968.26 | -$798.01 | -$1,762.79 | -$1,155.52 | -$1,106.03 |
| Max Winner | $4,338 | $2,374 | $10,526 | $8,406 | $2,427 |
| Max Loser | -$5,990 | -$3,947 | -$15,680 | -$6,497 | -$5,167 |

### Verdict by Change

#### 1. Equity-Based Sizing — CLEAR WINNER

- Nearly **doubled** total return: 137% → 271% (+134 pp)
- CAGR improved: 3.38% → **5.18%** (+53%)
- Sharpe and Sortino unchanged (0.45 / 0.61) — returns scaled proportionally with risk
- Same 1,258 trades — identical signal behavior, just better capital allocation
- Expectancy doubled ($109 → $216) — compound growth working as designed
- Downside: max loser grew to -$15,680 (vs -$5,990) — larger positions = larger absolute losses. Percentage losses are the same since position sizes scale with equity.

**Why it works:** The baseline bet a fixed $50K per trade regardless of equity. As the portfolio grew to $237K, it was still only deploying $50K. Equity sizing lets position sizes compound with profits.

#### 2. 5% Stop-Loss — ROUGHLY NEUTRAL (slightly harmful)

- Return barely changed: 137% → 144% (+6.8 pp, within noise)
- CAGR: 3.38% → 3.49% — marginal improvement
- Sharpe **degraded**: 0.45 → 0.38 — more noise, not better risk-adjusted returns
- **More trades**: 1,258 → 1,331 (+73) — stop-loss triggers exit early, then RSI re-enters on the next oversold reading. This churn generates commissions without adding value.
- Volatility **increased**: 7.97% → 10.00% — counterintuitive. Stop-losses on a mean reversion strategy create realized losses that would otherwise have reverted.
- Avg loser worsened: -$968 → -$1,156 — stops aren't preventing big losses on daily bars (the damage is already done by bar close)

**Why it fails:** RSI(2) mean reversion has a 1-3 day holding period. A 5% stop on daily bars triggers *after* the dip has already happened — too late to protect, but too early for the reversion thesis to play out. The stop is cutting off winning trades mid-reversion.

#### 3. SMA 200 Trend Filter — CLEARLY HARMFUL

- Return **more than halved**: 137% → 61% (-76 pp)
- CAGR: 3.38% → 1.85% — barely beats a savings account
- Trades slashed: 1,258 → 805 (-36%) — blocked 453 trades, many profitable
- Sharpe **degraded**: 0.45 → 0.35 — return reduction outweighed volatility reduction
- Max loser improved: -$5,990 → -$3,947 — avoided the worst bear market dips
- Win rate ticked up: 69.8% → 71.8% — remaining trades were slightly higher quality

**Why it fails:** The SMA(200) filter is designed for trend-following strategies. RSI(2) mean reversion profits from buying sharp panics that revert within days — these panics are *more* frequent and *more* pronounced in bear markets (below SMA 200). The filter blocks the strategy's best setups. The reduced volatility (7.97% → 4.67%) isn't worth the 55% return reduction.

### Conclusions

1. **Equity sizing is the only beneficial change.** It unlocks compounding — the single biggest driver of long-term returns. No downside on risk-adjusted basis.
2. **SMA filter is actively harmful** for this strategy archetype. Short-horizon mean reversion is fundamentally different from trend-following — trend filters destroy the edge.
3. **Stop-loss is noise** at 5% on daily bars. The granularity is too coarse for a 1-3 day holding period. A time-based stop (exit after N days) or intraday stop might work better.

### Recommended Config

Based on isolated testing, the optimal config uses **equity sizing only**:

```yaml
sma_trend_period: 0        # DISABLED - harmful for mean reversion
use_equity_sizing: true     # ENABLED - the key improvement
stop_loss_pct: 0.0          # DISABLED - neutral/harmful on daily bars
```

### Next Steps

1. **Set YAML to equity-only config** and commit as the new baseline
2. **Test equity + higher sell threshold** (0.65-0.70) — let winners run longer
3. **Test time-based stop** (5-10 day max hold) instead of price-based
4. **Walk-forward validation** — split into in-sample/out-of-sample to check for overfitting
