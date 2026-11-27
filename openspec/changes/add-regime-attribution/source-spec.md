# Regime Attribution Study — NTrader Spec

Nov 10, 2026 · @Allay Desai

## Purpose

This study answers one question: how much of each strategy's profit and loss depends on the market regime it traded in?

It labels the market's history once, by hindsight and by real-time rules, and then splits each strategy's backtest results across those labels. No strategy logic changes and nothing is optimized.

It comes before any regime filter or regime-detection model for two reasons:

- It shows whether regime matters enough for these strategies to be worth filtering at all.
- It shows which regimes the current strategies leave uncovered, which decides what strategy three should be.

## How it is used end to end

The regime work is built once for the market and then reused by every strategy. A new backtest adds no labelling work.

**Built once, with no strategy involved**

1. **Regime calendar.** Label the reference market's history twice: with hindsight (the ground truth) and with rules that use only past data (the detectors). Store both.
2. **Detector scorecard.** Measure how often each detector agrees with the ground truth and how late it is at turns.

**For every new strategy, following the vault pipeline**

1. **Idea.** Alongside the pass criteria, write the regime expectation: where the strategy should earn, where it should lose, and why.
2. **Backtest.** Run in-sample as today and apply gates G0 and G1. Nothing changes here.
3. **Regime profile.** Join the run's daily returns to the calendar by date. The result shows which regimes and which wave scale produced the profit.
4. **Check against the expectation.** A profile that matches the written expectation is evidence that the edge exists for the stated reason. Profit in an unexpected place is a warning that the result may be luck.
5. **Choose a path** from the table below.
6. **Robustness.** Apply G2 and G3 as today. A gate or sizing rule added in step 5 is part of the strategy and counts as trials.
7. **Portfolio fit.** Compare the profile with the strategies already held. Promote on what it adds to the portfolio: a strategy that earns where the others lose is worth more than a better copy of one you have.
8. **Paper and live.** The detectors label each new day. A drawdown in a regime where the profile predicts losses is expected; a drawdown in a regime where it should earn means something is broken.
9. **Feed back.** Regimes that no held strategy earns in become the search target for the next idea.

| Profile shows | Detector scorecard shows | Path |
| --- | --- | --- |
| No regime dependence | Not needed | Continue through the gates with no filter |
| Strong dependence | A detector tracks that regime with little lag | Test a gate or half-size rule in the Phase 2 filter test |
| Strong dependence | No detector tracks it in time | Add no filter; record the profile and find a partner strategy |

**What to expect from it**

The dependable benefits are steps 4, 7, 8 and 9: a check on overfitting, portfolio construction, live monitoring, and research direction. Step 5 is the least certain. Most filters fail the random-removal test, so plan for the third row of the table to be the common outcome.

Stop rule: if the trend and mean-reversion strategies show no dependence under the ground-truth labels, stop after build phase 2. The rest of the build would not pay for itself.

## Regime calendar

Regime labels belong to a market, not to a backtest. They are computed once per reference market, stored, and looked up by date.

Every strategy is scored against a shared calendar for three reasons:

- Comparing strategies by regime only works if they are measured on the same regime days.
- One stock's history split into buckets is too thin to trust, and hundreds of stocks give hundreds of chances to find a pattern by luck.
- Whether a single stock is above its own average is an entry condition that belongs in the strategy, not a regime.

Each traded asset maps to one reference. Start with SPY alone and add a reference only when a strategy trades something SPY clearly does not describe.

| Traded asset | Reference |
| --- | --- |
| US stocks and broad equity ETFs | SPY |
| Technology-heavy names and NQ | QQQ |
| Small caps | IWM |
| Bonds | TLT |
| Gold | GLD |

Storage rules:

- One row per reference, label set, definition version, and date.
- Detector labels are append-only. They use past data only, so a past day's label never changes.
- Ground-truth labels are blank for the most recent days and are filled in once the future they depend on has arrived.
- A changed definition creates a new version and never overwrites an old one, so earlier reports stay reproducible.
- Every report records the calendar version it used.

A later addition: the share of stocks above their 200-day average as a market-level breadth label. It uses the full stock universe but produces a single series.

## Scope and non-goals

Version 1 is a read-only analysis layer over completed backtest runs, plus the stored calendar it reads from.

In scope:

- A regime calendar for SPY holding ground-truth labels and detector labels.
- A detector scorecard that grades each detector against the ground truth.
- A regime profile for each strategy: results by ground-truth label and by detector label, on a daily and a per-trade basis.
- A with-trend versus counter-trend split of trades, which previews a direction gate before one is built.
- The reference market's own return in each bucket, to separate edge from market drift.
- Correlation between strategies, overall and inside each bucket.
- A later phase that tests one regime filter against a random baseline.

Out of scope for v1:

- Labelling individual stocks. Assets map to a reference market instead.
- Searching for the best trend definition, which is the separate vault idea "Trend definition sweep on SPY".
- Statistical jump models and trend-scanning labels. Both are planned additions, not part of the first build.
- Intraday regimes. Labels are daily, including for intraday strategies.
- Any change to order placement or to paper and live sessions.

The trend-definition panel, alignment split and drift benchmark came from the vault note on the Ironclad Trading video ([source](https://www.youtube.com/watch?v=BCf_M_Ybr2g)). Its claims are untested here and serve as questions, not evidence.

## Ground-truth labels

The ground truth is built with hindsight: each day is labelled by the wave it turned out to sit in. It measures how much regime matters and is never used to trade.

**Wave legs.** A turning point is confirmed when price reverses from its extreme by a set threshold. Every day between a trough and the next peak is an up-leg day; every day between a peak and the next trough is a down-leg day. This is the rule used in academic bull and bear market dating (Lunde and Timmermann).

There is one ground truth per scale, so the rule runs at three thresholds. Each threshold is a multiple of the reference's 20-day average true range, which adapts it to the market's volatility.

| Scale | Reversal threshold | Roughly corresponds to |
| --- | --- | --- |
| Minor | 3 × average true range | Pullbacks and bounces |
| Intermediate | 6 × average true range | Swings lasting weeks |
| Major | 12 × average true range | Corrections and bear markets |

Each day therefore carries three direction labels. Together they give the multi-timeframe reading: a major up-leg with a minor down-leg is a pullback in an uptrend.

**Ranging.** A wave rule has no label for chop, so a separate flag marks it. A day is ranging when the efficiency ratio over the 21 days centered on it is below 0.3. The efficiency ratio is the absolute net price change divided by the sum of absolute daily changes.

**Volatility.** High when realized volatility over the same centered 21 days is above its full-sample median, otherwise Low.

Rules that keep it a diagnostic:

- Ground-truth labels apply to the same day's return, with no lag.
- They are never exported to a strategy config, a sweep, or a filter.
- Every report built from them is stamped "hindsight, not tradeable".
- The thresholds above are starting values, fixed before any strategy result is seen.
- Days after the last confirmed turning point at a scale have no direction label at that scale yet.

Two limits to keep in mind:

- **Circularity.** A trend strategy earns in up-legs partly by construction. The useful numbers are the size of its losses elsewhere and whether another strategy earns them back.
- **Thin major scale.** A decade or two of history holds only a handful of major down-legs. Major-scale findings are weak evidence, and the minimum sample rule will often mark them insufficient.

A later cross-check: label the same history with trend scanning (López de Prado), which fits forward-looking regressions and has its own no-trend class. Days where the two methods disagree are excluded from verdicts.

## Real-time detectors

A detector is a rule that labels each day using only past data. It is what a strategy could actually act on, and it is graded against the ground truth.

Four trend detectors are fixed here. None of them is assumed to be right.

| Detector | Up when |
| --- | --- |
| Long average | Close is above its 200-day simple moving average |
| Medium average | Close is above its 50-day simple moving average |
| Channel position | Close is in the upper half of its 100-day high-low range |
| Swing structure | The latest confirmed swing high and swing low are both above the ones before them |

A swing point is a bar whose high or low is the extreme of the 5 bars on each side. It counts only once those 5 later bars have printed.

One volatility detector: High when 20-day realized volatility is above its own trailing 252-day median, otherwise Low. Realized volatility is the standard deviation of daily close-to-close returns, annualized.

Rules that prevent lookahead:

- A label for day *t* uses only data up to the close of day *t*.
- The label from day *t* is applied to the strategy's return on day *t+1*.
- The volatility median is trailing, never computed over the full sample.

The first 272 trading days of reference data are warm-up and carry no detector label.

### Detector scorecard

Each detector is scored against the ground truth at each of the three scales. The scorecard involves no strategy, so it is computed once per reference market.

| Measure | Definition |
| --- | --- |
| Agreement | Share of days on which the detector's direction matches the ground-truth leg |
| Lag at turns | Median trading days between a ground-truth turning point and the detector's flip |
| Flips per year | How often the detector changes state, a measure of whipsaw |

The scorecard shows which scale each detector tracks. A slow detector such as the 200-day average should match the major scale and miss the minor one; if it matches nothing well, it is not a usable regime signal.

The detectors are a panel, not a menu. No detector is adopted because it flatters a strategy, and all four count as trials in the study record. Searching for the best definition stays in the separate vault idea "Trend definition sweep on SPY".

Later challenger: a statistical jump model, which penalizes each regime switch to keep regimes persistent. It enters as a fifth detector and is scored by the same card.

## Inputs

The study needs three inputs per run, all from data NTrader already has or can export.

| Input | Detail |
| --- | --- |
| Daily strategy returns | Mark-to-market equity per day, net of commissions and slippage |
| Trade list | Entry time, exit time, side, and profit or loss per trade |
| Reference bars | Daily closes for the reference instrument, starting at least 272 trading days before the backtest begins |

First runs to analyze: the existing trend strategy and the existing mean-reversion strategy, over the same date range.

Minimum sample per bucket: 60 trading days and 20 trades. A bucket below either limit is reported but marked insufficient, and no conclusion is drawn from it.

## Method

A run's regime profile is built by joining its results to the stored calendar. The same results are split two ways: by day, which is exact, and by trade, which is easier to act on.

1. Look up the strategy's reference market and load its calendar rows for the run's dates.
2. Align the strategy's daily returns to the labels by date. Detector labels are already shifted one day; ground-truth labels are not.
3. **Daily view.** Group daily returns by label and compute the metrics in the Outputs section. A multi-day trade contributes to every label it passes through.
4. **Trade view.** Assign each trade to the label in force at its entry, then compute per-trade metrics.
5. **Alignment.** Tag each trade as with-trend (long while the label is Up, or short while it is Down) or counter-trend, and compute the trade-view metrics for each group.
6. **Exposure and drift.** Record the share of days in each bucket on which the strategy held a position, and the reference market's own annualized return on those days.
7. **Uncertainty.** For each bucket, compute a 90% confidence interval on mean daily return using a block bootstrap: 1,000 resamples, 10-day blocks. Blocks are needed because regime days cluster and are not independent.
8. **Across strategies.** Compute the correlation of daily returns between strategies, overall and per bucket. Then build an equal-risk combination and repeat step 3 on it.

Steps 3 to 7 run once per label set:

- **Ground truth:** leg direction at each of the three scales, the ranging flag, and volatility, each on its own. Only the scale with the widest gap between up-leg and down-leg results is also crossed with volatility, to keep buckets from thinning out.
- **Detectors:** each of the four trend detectors crossed with the volatility detector, giving four buckets per detector.

## Outputs

Each run produces one table per strategy with a row per bucket plus a total row, a correlation table, and a short written verdict.

Daily-view columns, per bucket:

| Metric | Definition |
| --- | --- |
| Days | Count of labelled days in the bucket |
| Time share | Days in the bucket as a percentage of all labelled days |
| P&L share | Bucket profit or loss as a percentage of total |
| Annualized return | Mean daily return × 252 |
| Annualized volatility | Standard deviation of daily returns × √252 |
| Sharpe | Annualized return divided by annualized volatility |
| 90% interval | Bootstrap interval on mean daily return |
| Worst day | Lowest single-day return |
| Exposure | Percentage of days holding a position |

Trade-view columns, per bucket:

| Metric | Definition |
| --- | --- |
| Trades | Count of trades entered in the bucket |
| Win rate | Winning trades as a percentage of trades |
| Average win and average loss | Mean profit of winners and mean loss of losers |
| Expectancy | Mean profit or loss per trade |
| Profit factor | Gross profit divided by gross loss |

Added columns and tables:

| Output | Definition |
| --- | --- |
| Benchmark return | Daily view, per bucket: the reference instrument's annualized return on the same days |
| Alignment table | Trade-view metrics for with-trend trades and for counter-trend trades |
| Panel table | One row per trend definition, showing the verdict reached under it |

For a long-only strategy, with-trend trades are simply the trades entered in Up buckets. The alignment table adds information only for strategies that trade both sides.

Each strategy's tables together form its **regime profile**, saved with the run and stamped with the calendar version. The profile is what later steps reuse: the portfolio comparison, the filter test and live monitoring.

The **detector scorecard** is a separate output, produced once per reference market and refreshed when the calendar gains a new version.

Correlation output: one strategy-by-strategy matrix for the full period and one per bucket.

Files: a JSON result for later tooling and a Markdown report saved as an experiment note in the Trading Research vault.

## Reading the results

The verdict for each strategy follows fixed rules, written here before any result exists.

| What the table shows | What it means | Next action |
| --- | --- | --- |
| No bucket has an interval entirely below zero | Regime is not hurting this strategy | Do not filter; rely on diversification |
| One bucket with enough sample has an interval entirely below zero | The strategy loses reliably in that regime | Take it to the Phase 2 filter test |
| More than 70% of profit comes from a bucket with under 30% of the time | The edge is regime-dependent and fragile | Pair it with a strategy that earns in the other buckets |
| Two strategies lose in the same bucket | The portfolio has an uncovered regime | Choose strategy three to earn in that bucket |
| Bucket differences sit inside overlapping intervals | The differences are noise | Draw no conclusion; collect more history or instruments |

The 70% and 30% thresholds are judgment calls, not statistical tests. They exist to stop the reading from shifting after the numbers are in.

Four more rules apply to the added outputs:

| What the output shows | What it means | Next action |
| --- | --- | --- |
| A verdict holds under at least three of the four trend definitions | The finding is about trend, not about one indicator | Act on it as the table above says |
| A verdict holds under two or fewer | The finding depends on the definition | Draw no conclusion, and do not adopt the definition that gave it |
| With-trend expectancy is above counter-trend, with intervals that do not overlap | A direction gate is worth building | Take the gate to the Phase 2 filter test |
| A long strategy's bucket return is no higher than its exposure times the benchmark return | The profit in that bucket is market drift, not edge | Do not count that bucket as evidence for the strategy |

A verdict applies only to the strategy it was measured on. The vault already records a 200-day filter hurting RSI(2) mean reversion on QQQ, so a bucket that hurts one strategy can be the profitable one for another.

### Comparing ground truth with detectors

The rules above are read on detector labels, because those are what a strategy can act on. The ground-truth profile is read first, to decide whether the detector results are worth acting on at all.

A strategy shows strong dependence under a label set when at least one bucket has an interval entirely below zero and another has one entirely above.

| Ground-truth profile | Detector profile | Conclusion |
| --- | --- | --- |
| Weak dependence at every scale | Not needed | Regime does not matter for this strategy; stop regime work on it |
| Strong dependence | Strong dependence under a detector the scorecard rates well at that scale | The regime is detectable; go to the Phase 2 filter test |
| Strong dependence | Weak dependence under every detector | Regime matters but is detected too late; add no filter and find a partner strategy |

The scale at which the ground-truth dependence is strongest is itself a finding. It says which wave size drives the strategy, and so which detector could ever help it.

The third row is where a jump model or other learned detector could add value. Its test is whether it narrows the gap between the two profiles on out-of-sample data.

## Phase 2: filter test

A regime filter counts as an edge only if it beats removing the same amount of trading at random.

This phase runs only for a strategy that the attribution flagged, and only on the flagged bucket.

1. Split the history in half by date. Confirm the losing bucket using the first half only.
2. Define the filter from that half: in the flagged bucket, trade at half size or not at all.
3. Apply the filter to the second half and record Sharpe and maximum drawdown.
4. Build the baseline: 1,000 versions of the second half, each removing the same number of trading days at random, in runs matched to the typical length of a regime spell.
5. The filter passes if its second-half Sharpe and maximum drawdown both beat the 95th percentile of the random versions.
6. Repeat on at least one other instrument or period. A filter that passes once is still a candidate, not a rule.

Prefer half size over fully off. Scaling down keeps the strategy trading through label errors and regime flips.

## NTrader implementation

The NTrader research MCP server already covers part of this study, so the build is an extension of existing tools, not a new one. Tool and parameter names below are proposals, to be matched to the server's conventions.

| Need | Existing tool | Gap to close |
| --- | --- | --- |
| Results by trend and by volatility | `get_regime_breakdown` | Computes its own labels from the run's symbol on each call; it needs to read the stored calendar and report joint buckets |
| Correlation and combined results across runs | `get_sleeve_correlation` | Correlation is for the full period; the study needs it per bucket |
| Stored regime calendar | None | New |
| Ground-truth wave labels | None | New |
| Detector scorecard | None | New |
| Confidence intervals per bucket | None | New |
| Filter test against random removal | None | New |

The proposed tools below can be new tools or new options on the two existing ones. Run both existing tools on the trend and mean-reversion runs first; they give a rough answer today.

**`regime_labels`** builds and stores the regime calendar for a reference market, and returns the dated label series from it. Every other tool reads labels from the calendar and computes none of its own.

**`regime_attribution`** takes one or more completed backtest run IDs and returns the tables in the Outputs section.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `run_ids` | required | Backtest runs to analyze |
| `reference` | `SPY` | Instrument the labels are built from |
| `trend_window` | 200 | Days in the moving average |
| `vol_window` | 20 | Days in the realized volatility window |
| `vol_lookback` | 252 | Days in the trailing volatility median |
| `lag_days` | 1 | Days between a label and the return it is applied to |
| `min_days` | 60 | Day count below which a bucket is marked insufficient |
| `min_trades` | 20 | Trade count below which a bucket is marked insufficient |
| `bootstrap_samples` | 1000 | Resamples for the confidence interval |

Build phases:

1. **Calendar.** Ground-truth labels and the long-average detector for SPY, stored and versioned. Exit check: a rebuild reproduces the stored labels exactly, and the wave legs look right on a chart.
2. **Regime profile.** Daily view of one run against the calendar. Run it on the trend and mean-reversion strategies. Exit check: bucket P&L sums to the run's total. This is the stop-or-continue point described in the end-to-end section.
3. **Detector panel and scorecard.** The other three trend detectors, the volatility detector, and their scores against the ground truth.
4. **Trade view, alignment and intervals.** Exit check: trade counts sum to the run's trade count.
5. **Multiple runs.** Per-bucket correlation and the equal-risk combination.
6. **Filter test** from Phase 2 of this spec.

Later additions, each behind the same calendar: more reference markets, trend-scanning labels, a jump-model detector, and a breadth label.

Defaults are the values fixed in this spec. A run that overrides them records the override in its report. One further parameter, \`label\_set\`, selects the detector labels (the default) or the ground-truth labels.

## Validation and pitfalls

The tool is trusted only after it passes four checks.

- [ ] **Reconciliation.** Bucket P&L sums to total P&L, and bucket days sum to labelled days.
- [ ] **No lookahead.** A unit test confirms the label for day *t* is unchanged when data after day *t* is altered.
- [ ] **Lag matters.** Results with a lag of 0 and a lag of 1 differ. If they are identical, the lag is not being applied.
- [ ] **Known case.** A buy-and-hold SPY run shows higher volatility in the two High buckets than in the two Low buckets. If it does not, dates are misaligned.

Pitfalls to avoid when using it:

- **Tuning thresholds to the result.** Changing the 200-day or 20-day settings until a bucket looks bad is overfitting. Change them only as a declared robustness check.
- **Reading thin buckets.** Down-trend buckets hold fewer days than up-trend ones in most equity history. Respect the insufficient flag.
- **Adding dimensions.** Each new yes-or-no dimension doubles the bucket count and halves the sample in each. Stay at four buckets until they are understood.
- **Trusting the trade view alone.** A trade entered in one regime can exit in another. The daily view is the reference; the trade view is the summary.

* **Adopting the best panel definition.** Four definitions on one history is already four tries. Using the one that flatters a strategy turns a robustness check into a parameter search.

- **Leaking ground truth into a strategy.** The wave labels know the future. If one ever reaches a strategy config or a filter, every result built on it is invalid.
- **Treating the ceiling as a target.** A turning point is confirmed only after price has reversed by the threshold, so no detector can capture a whole leg. The ground-truth profile is an upper bound.

## Open questions

- [ ] Does NTrader store daily mark-to-market equity for each run, or only the trade list? If only trades, Phase 1 needs a daily equity export first.
- [ ] Is the reference map in the Regime calendar section right for what you trade? It replaces the earlier question of SPY versus each strategy's own instrument.
- [ ] Which two backtest runs are the trend and mean-reversion baselines, and do they cover the same dates?
- [ ] For the volatility label, is realized volatility acceptable, or should VIX be offered as an alternative?
- [ ] For the NQ and ES work, should the reference be the future itself, with the same daily rules?

* [ ] Does `get_regime_breakdown` lag its labels by a day, and are its volatility terciles trailing or computed over the full sample? Full-sample terciles use future data, which would make its current output closer to a hindsight view.

- [ ] Should the first alignment run be the existing ORB runs on SPY? If with-trend ORB trades are no better than counter-trend ones, the "Daily trend gate on SPY ORB" idea can be dropped before any code is written.
- [ ] Do verdicts differ between SPY and QQQ as the reference? The video claims the best trend definition is market-specific; the vault note flags this as possibly noise.
- [ ] Are hourly trend labels worth a later version? They are out of scope here, and the video's claim that they lead daily labels is untested.

* [ ] Do the wave thresholds of 3, 6 and 12 times the average true range give sensible legs on SPY? Check the leg count per scale on a chart before fixing them.
* [ ] Where should the calendar live: in NTrader's database beside the catalogs, or as files under `~/.ntrader`?
* [ ] Should the regime profile become part of the promotion gates, for example a G1 note that the profile matches the written expectation? The gates are yours to change.
* [ ] Should the Idea template gain a "Regime expectation" line, so step 1 of the per-strategy process is not skipped?

## Sources

The structure of this study follows published work. These were consulted through search summaries on 25 November 2026, not read in full, and their performance results are the authors' own claims.

- [How to identify and forecast bull and bear markets](https://repub.eur.nl/pub/41558/ERS-2013-016-F&A.pdf): compares the Pagan and Sossounov and the Lunde and Timmermann dating rules.
- [Maheu, McCurdy and Song](https://www.economics.utoronto.ca/public/workingPapers/tecipa-402.pdf): bull corrections and bear rallies inside larger regimes.
- [Trend scanning labels](https://random-docs.readthedocs.io/en/latest/implementations/labeling_trend_scanning.html): López de Prado's forward-looking labelling method.
- [Shu, Yu and Mulvey](https://arxiv.org/html/2402.05272v3): statistical jump models compared with hidden Markov models.
- [Dynamic asset allocation with asset-specific regime forecasts](https://arxiv.org/html/2406.09578v1): label history first, then forecast the labels.
- [Algorithmic trading with directional changes](https://kar.kent.ac.uk/id/document/3247271): threshold-based trend summaries at several scales.
- [Ironclad Trading video](https://www.youtube.com/watch?v=BCf_M_Ybr2g): source of the trend-definition questions, via the vault note.
