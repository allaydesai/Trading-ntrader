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

1. ~~**Set YAML to equity-only config** and commit as the new baseline~~ **DONE** (commit `3a51dbd`)
2. **Test equity + higher sell threshold** (0.65-0.70) — let winners run longer
3. **Test time-based stop** (5-10 day max hold) instead of price-based
4. **Walk-forward validation** — split into in-sample/out-of-sample to check for overfitting

---

## Refinement Plan — Research-Informed Next Steps

**Date:** 2026-02-23
**Current baseline:** Equity sizing enabled, SMA/stop-loss disabled (5.18% CAGR, 0.45 Sharpe)

### Literature Review Summary

Research into Connors' RSI(2) strategy and recent practitioner studies (Quantitativo, StockSoft, Alvarez Quant Trading) reveals:

1. **The RSI(2) edge is decaying.** Multiple independent studies confirm underperformance vs benchmark in 2021-2024 (7.1% vs 13.4% Nasdaq-100). More algos front-run the mean-reversion bounce, compressing the snap-back.

2. **Sell threshold of 0.50 is too conservative.** Connors' original work recommends exiting at RSI(2) > 0.65, or when price closes above yesterday's high. Exiting at 0.50 (median) leaves money on the table.

3. **Cumulative RSI outperforms standard RSI on risk-adjusted basis.** Head-to-head (Quantitativo, 1998-2024): nearly identical annual returns but **37% max drawdown vs 57%**, Sharpe 1.18 vs 1.05. Filters ~half the trades, nearly doubles expected return per trade.

4. **SMA filter result aligns with literature.** The strongest oversold bounces happen *in bear markets* (below SMA 200), confirming our finding that the filter destroys edge for short-horizon mean reversion.

5. **Buy threshold may need loosening.** Post-2015 data shows optimal entry threshold has shifted upward — RSI < 15-20 entries now outperform RSI < 5-10 entries as extreme readings are rarer and snap-backs weaker.

6. **"Close above yesterday's high" exit** produces lower drawdowns than RSI-based exits (Quantitativo). Captures the reversion quickly (1-2 days) with less tail risk than waiting for RSI recovery.

7. **Higher volatility = better mean reversion returns.** Larger price dislocations produce larger snap-backs, which the strategy already exploits implicitly.

### Prioritized Test Plan

#### Phase 1 — Config-only changes (no code modifications)

| # | Change | Current | Test Value | Rationale | Risk |
|---|--------|---------|------------|-----------|------|
| 1 | Sell threshold | 0.50 | **0.65** | Let winners run; Connors' recommended exit. Should increase avg winner and profit factor | Low — trade stays in slightly longer but RSI(2) moves fast |
| 2 | Buy threshold | 0.10 | **0.15** | Adapt to modern regime; post-2015 data shows higher thresholds capture more opportunities with similar edge | Low — more trades with slightly lower per-trade edge |

**Test method:** Isolated backtests (one change at a time), same methodology as Refinement Run #2.

#### Phase 2 — Validation

| # | Change | Type | Rationale |
|---|--------|------|-----------|
| 3 | Walk-forward split | Analysis | Split 2000-2015 (in-sample) / 2016-2025 (out-of-sample). If CAGR and Sharpe degrade significantly out-of-sample, the edge may be fading. Critical before further parameter changes |

#### Phase 3 — Code changes (strategy modifications)

| # | Change | Implementation | Expected Impact | Risk |
|---|--------|---------------|-----------------|------|
| 4 | **Cumulative RSI** | Maintain rolling window of RSI(2) values (size 2), sum for signal. Buy when cumRSI < 0.35, sell when cumRSI > 0.65 | Better Sharpe, ~35% lower drawdown, fewer but higher-quality trades | Fewer trades = lower sample rate |
| 5 | **"Close > yesterday's high" exit** | Track `previous_bar.high` in `on_bar()`, add config flag `exit_mode: "rsi" \| "prev_high"` | Faster exits, lower drawdown, less tail risk | Lower per-trade profit |
| 6 | **Time-based exit (10-day max hold)** | Track bars since entry, force exit after N days. Add `max_hold_days` config parameter | Safety valve for trapped positions; unlike price stop-loss, doesn't fight mean-reversion thesis | May exit positions that would have recovered on day 6+ |

#### Deprioritized (future consideration)

- **Multi-threshold diversification** — Spread entries across RSI 5/10/15/20/25/30 with Sharpe-based monthly rebalancing. Promising (Quantitativo: 25.7% annual, Sharpe 1.14) but requires significant architectural changes.
- **VIX-based filtering** — Adds complexity and second data feed. Volatility relationship already works in the strategy's favor implicitly.
- **Bollinger/ADX combination signals** — Insufficient empirical evidence that combining indicators improves over tuned RSI(2) alone.
- **Connors RSI (CRSI) composite** — Combines RSI(2) + streak RSI + percent rank. Profit factor 2.08 over 288 trades (75% win rate). Worth exploring after Phase 3.

### Key Sources

- Connors & Alvarez, *Short Term Trading Strategies That Work* (2009) — original RSI(2) system
- Quantitativo, "Trading the Mean Reversion Curve" (2024) — edge decay evidence, cumulative RSI comparison, multi-threshold approach
- StockSoft Research, "RSI 2 Trading Strategy on US Stocks" — performance degradation 1995-2016
- Alvarez Quant Trading, "ABCs of Mean Reversion" — regime filtering, position sizing
- MQL5, "Day Trading Connors RSI2 Strategies" — exit strategy comparison

---

## Phase 1 Results — Threshold Optimization

**Date:** 2026-02-24
**Baseline:** Equity sizing enabled, SMA/stop-loss disabled (buy 0.10, sell 0.50)

### Run IDs

| Test | Run ID |
|------|--------|
| Sell 0.65 | `690af833-fc68-4021-ae02-73049c7fb027` |
| Buy 0.15 | `25e82f87-f5e4-4d11-af1d-bbcf96ffcf8e` |
| Combined | `280e7726-a7e1-40a3-9ce1-961d20320b38` |

### Full Comparison Table

| Metric | Baseline | Sell 0.65 | Buy 0.15 | Combined |
|--------|----------|-----------|----------|----------|
| **Total Return** | 271.34% | **306.17%** | 288.23% | **317.50%** |
| **CAGR** | 5.18% | **~5.54%** | ~5.35% | **~5.65%** |
| **Final Balance** | $371,342 | $406,169 | $388,225 | **$417,505** |
| **Sharpe Ratio** | 0.45 | **0.476** | 0.453 | 0.471 |
| **Sortino Ratio** | 0.61 | **0.647** | 0.609 | 0.634 |
| **Profit Factor** | 1.40 | **1.427** | 1.390 | 1.410 |
| Volatility | 7.97% | 8.06% | 8.28% | 8.35% |
| Total Trades | 1,258 | 1,237 | 1,337 | 1,314 |
| Win Rate | 69.7% | 70.0% | 69.7% | 69.9% |
| Expectancy | $215.52 | **$247.31** | $215.41 | $241.45 |
| Avg Winner | $1,077 | **$1,172** | $1,081 | $1,161 |
| Avg Loser | -$1,763 | -$1,905 | -$1,779 | -$1,894 |
| Max Winner | $10,526 | $11,613 | $11,011 | $11,963 |
| Max Loser | -$15,680 | -$17,330 | -$16,441 | -$17,838 |
| Commissions | ~$12K | $13,585 | $14,294 | $14,355 |

### Verdict by Change

#### 1. Sell Threshold 0.65 — CLEAR WINNER (best risk-adjusted improvement)

- **+35 pp total return** (271% → 306%) with FEWER trades (1,237 vs 1,258)
- Sharpe improved **0.45 → 0.476** (+5.8%) — genuine risk-adjusted improvement
- Sortino improved **0.61 → 0.647** (+6.1%)
- Profit Factor improved **1.40 → 1.427** (+1.9%)
- Expectancy improved **$216 → $247** (+14.8%) — each trade is more valuable
- Avg winner increased **$1,077 → $1,172** (+8.8%) — letting winners run longer works exactly as predicted
- Avg loser worsened modestly **$1,763 → $1,905** (+8.1%) — positions held slightly longer through drawdowns too
- Consistent with Connors' original research: RSI > 0.65 is his recommended exit threshold

**Why it works:** At sell threshold 0.50 (median RSI), the strategy was exiting as soon as the bounce started. Raising to 0.65 captures more of the reversion move — the RSI typically overshoots past 0.50 on a bounce, and exiting at 0.65 captures that overshoot as profit.

#### 2. Buy Threshold 0.15 — MARGINAL (volume improvement, not quality)

- **+17 pp total return** (271% → 288%) through more trades (1,337 vs 1,258, +6.3%)
- Sharpe essentially flat: **0.45 → 0.453** — return improvement is from volume, not edge quality
- Profit Factor actually **worsened slightly**: 1.40 → 1.39
- Expectancy unchanged at **$215** — new trades at RSI 0.10-0.15 have the same average edge
- The additional ~79 trades capture entries where RSI dips to 0.10-0.15 but not below 0.10 — these are less extreme oversold readings with statistically identical edge quality

**Why it's marginal:** The trades between RSI 0.10 and 0.15 are neither better nor worse than the existing set. They add return through sheer volume but don't improve the strategy's risk profile.

#### 3. Combined (Sell 0.65 + Buy 0.15) — Best absolute return

- **+46 pp total return** (271% → 318%) — highest of all tests
- Sharpe **0.471** — slightly below sell-only (0.476) but above baseline (0.45)
- The sell threshold provides the quality improvement; the buy threshold provides additional volume
- On risk-adjusted basis, the combined is slightly diluted vs sell-only because the looser buy adds marginal trades

### Conclusions

1. **Sell threshold 0.65 is the highest-leverage change.** It improves every risk-adjusted metric while reducing trade count. This aligns with Connors' original research.
2. **Buy threshold 0.15 adds volume without improving quality.** It's a "more of the same" change — not harmful, but not transformative.
3. **Combined gives the best absolute return** ($417K vs $406K vs $388K vs $371K) but slightly dilutes risk-adjusted metrics compared to sell-only.

### Recommended Config Update

**Sell threshold 0.65** should be adopted. The buy threshold change is optional — adopt if maximizing absolute return is the priority; skip if optimizing for Sharpe.

```yaml
buy_threshold: 0.10           # Keep (or optionally raise to 0.15 for +17pp)
sell_threshold: 0.65          # CHANGE from 0.50 — +35pp return, +5.8% Sharpe
```

### Cumulative Improvement from Baseline

| Stage | Total Return | CAGR | Sharpe | Delta |
|-------|-------------|------|--------|-------|
| Original baseline (fixed sizing) | 137% | 3.38% | 0.45 | — |
| + Equity sizing | 271% | 5.18% | 0.45 | +134 pp |
| + Sell threshold 0.65 | 306% | ~5.54% | 0.476 | +35 pp |
| + Buy threshold 0.15 (optional) | 318% | ~5.65% | 0.471 | +12 pp |

Total improvement from original: **+180 pp return, +2.27 pp CAGR, +0.026 Sharpe** (or +0.021 with both thresholds).
