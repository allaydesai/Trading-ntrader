# NTrader Research MCP — Product Requirements

Oct 3, 2026 · @Allay Desai

The research MCP lets Allay and Claude build a portfolio of strategies one tested strategy at a time: each idea is improved run after run with proof that the gain is real, not fitted to the past, before it earns a place in the portfolio. It ships in phases, and every phase is a working tool.

## End goal: a portfolio of strategies

The end goal is a portfolio of systematic strategies across asset types, fed by daily scans, whose combined result is what matters; each strategy is still researched and proven on its own before it joins.

&#91;embedded content: end goal · scans, strategy sleeves, portfolio\]

- **One idea, one strategy.** Research happens per idea: a study ends in one strategy (a "sleeve") with its own evidence.
- **Different styles on purpose.** Trend-following and mean-reverting sleeves win in different market regimes; holding both is meant to smooth the combined equity curve. A new sleeve is judged partly on what it adds to the ones already held — low correlation can matter more than a high standalone Sharpe.
- **Daily scans feed the sleeves.** Each sleeve has a scan that finds today's candidates in its universe (stocks, ETFs, later other assets); research tests the scan and the rules together, as a universe, not a single hand-picked chart.
- **The portfolio has its own rules.** Allocation per sleeve, total risk and exposure limits are tested on the combined result, the same way a single strategy is tested.

NTrader backtests one strategy at a time today; multi-strategy portfolio support is on its roadmap. The phases below start with single strategies and add the portfolio layer when the engine supports it.

## Problem, user and success measures

Today a strategy improves by running more backtests and keeping the best one, which is exactly how overfitting happens; the product must make the honest path the easy path.

**The user.** Allay, a systematic retail trader who builds his own tools. He works in short sessions, mostly by voice, with Claude as research partner: Claude proposes and runs experiments, Allay decides. NTrader is the engine; the Trading Research Obsidian vault is the record.

**What goes wrong today**

- Every run is a one-off. Nothing counts how many parameter sets were tried, so a lucky best-of-200 looks as convincing as a first try.
- There is no protected holdout. The Apolo RSI(2) work tuned eight variants on the full 2000–2025 history, so no clean out-of-sample data is left for it.
- Results are single numbers. A Sharpe of 0.48 says nothing about how bad the next drawdown could be or how likely a losing year is.
- Robustness checks (sensitivity, sub-periods, walk-forward) are possible only by hand, so they get skipped.
- Claude cannot run backtests at all without the Mac's native environment.

**Success measures**

| Measure | Target |
| --- | --- |
| Time from a captured idea to a first in-sample backtest with benchmark | < 15 minutes of Allay's attention |
| Promoted strategies with a frozen candidate, one out-of-sample run, walk-forward, Monte Carlo band and regime table on record | 100 % |
| Out-of-sample runs executed more than once per frozen candidate without a recorded override | 0 |
| Every run attributable to a study, with its trial count, git commit and config | 100 % |
| Paper sessions whose results are checked against the backtest's expectation band at least weekly | 100 % |
| Strategies that pass all gates but break their Monte Carlo drawdown band within the first 8 weeks of paper | Tracked; any occurrence triggers a review of the gates |
| Each phase usable on its own: scenario steps for that phase run from a Claude chat and land in the vault | Every phase, before the next starts |
| Sleeves admitted to the portfolio on evidence that the combined result improves (phase 5) | 100 % |

## Core concepts

The **study** is the new central object: every run belongs to one, and the study is what remembers the holdout, the trial count and the decisions, so discipline does not depend on anyone's memory.

| Concept | What it is | Why it exists |
| --- | --- | --- |
| **Study** | One research question about one idea: hypothesis, strategy, instruments, parameter space, data split, pass criteria, trial budget. Maps 1:1 to an Idea note in the vault | Groups every run that bears on the question, so evidence and trial counts are never scattered |
| **Holdout (data split)** | In-sample window for exploration and tuning; out-of-sample window locked when the study is created | Out-of-sample data stays unseen until a candidate is frozen |
| **Trial ledger** | Every run in the study, counted, with its parameters and in-sample result | The number of attempts discounts the best result (deflated Sharpe) |
| **Trial budget** | Maximum in-sample runs before the study needs a recorded reason to continue | Makes "just one more sweep" a visible decision |
| **Candidate** | A specific parameter set chosen from exploration, frozen with its code commit and config hash | What gets tested out-of-sample, walk-forward and on paper; it cannot drift afterwards |
| **Out-of-sample run** | One run of a frozen candidate on the locked window | The cheapest honest test; repeating it is refused unless an override is recorded |
| **Contamination** | A study flag set when anything is changed after out-of-sample data was seen | Tells everyone later that the out-of-sample result is no longer clean evidence |
| **Variability analyses** | Monte Carlo on trades, metric confidence intervals, regime and sub-period splits, parameter sensitivity, breadth across instruments | Turn one backtest number into a range of outcomes |
| **Scorecard** | A candidate's evidence against each gate in the vault's `System/Gates.md`: pass, fail or missing | One place to decide promote, iterate or reject |
| **Expectation band** | The range of results a candidate should produce over a given number of trades or weeks, from Monte Carlo and walk-forward | The yardstick for paper trading: inside the band is normal, outside is a flag |
| **Sleeve** | A validated strategy as a component of the portfolio, with its own scan, universe and allocation | Each idea is proven alone, then judged on what it adds to the whole |
| **Scan** | The daily rule that picks a sleeve's candidates from its universe | Tests the strategy as it will trade — on what the scan finds, not on hand-picked charts |
| **Portfolio** | The set of sleeves with allocation, total-risk and exposure rules, backtested as one combined result | The combined result is the number that matters in the end |

## The research loop

The loop is organised by which data each step may see: exploration is free on in-sample data, the out-of-sample window is spent once per frozen candidate, and paper trading is the only test on truly new data.

&#91;embedded content: research loop · 10 steps across 3 data windows\]

A failed gate sends the work back to exploration as a new candidate version, but the out-of-sample window cannot be un-seen, so that version must prove itself by walk-forward and on paper instead.

## Scenario: Cumulative RSI(2) on QQQ, from blog post to paper trading

Over about a week of short sessions, one idea goes from a link to a paper-trading session, and at every step the product either produces evidence or blocks a shortcut. This is the target state; the phased delivery section shows which steps work after each phase (steps 4–5 work from phase 1). Story ids refer to the next section; all numbers are examples, not results.

**Session 1 — from link to baseline (about 30 minutes)**

1. **Capture.** Allay: *"Found this post on cumulative RSI — it claims the same returns as RSI(2) with much smaller drawdowns."* Claude reads the post, files a Source and an Idea in the vault, and flags that the post shows no costs and no out-of-sample test. *(vault skill; no MCP)*
2. **Set up the study.** Claude proposes the study: hypothesis, QQQ daily bars, parameter space (window 2–3, entry 0.25–0.45, exit 0.55–0.75), in-sample 2000–2015, out-of-sample 2016–2025, pass criteria from the gates, trial budget 40. Allay says "go". The out-of-sample window is now locked. *S1.1, S1.2, S1.3*
3. **Probe the concept.** Before writing any strategy code, Claude asks whether the effect exists at all: it pulls in-sample bars only and compares 1-, 3- and 5-day forward returns after a cumulative-RSI washout with all days, split by sub-period. The effect is positive in each sub-period, so building is worth it. *S2.5, S4.3*
4. **Build and check.** Claude writes the strategy file, then confirms the strategy registers, its parameters validate and QQQ data covers the window — before any run. *S1.4*
5. **Baseline.** Three jobs go in at once: the post's default parameters, QQQ buy-and-hold and the incumbent Apolo RSI(2), all on the in-sample window. Allay keeps talking while they run; Claude reports when they finish with a side-by-side table. Trial ledger: 1 of 40. *S2.1, S2.2, S2.4, S3.3*

**Session 2 — explore and measure variability (about 30 minutes)**

6. **Explore.** Claude proposes a coarse grid of 30 combinations (fits the remaining budget) and runs it in-sample. The sensitivity map shows a broad plateau around entry 0.30–0.40, exit 0.65, and one isolated spike at entry 0.45. Claude recommends the plateau centre, not the spike, and explains why. Ledger: 31 of 40. *S3.1, S3.2, S3.3*
7. **Measure variability.** On the plateau candidate, still in-sample: Monte Carlo over 5,000 trade resamples gives a CAGR range (5th–95th percentile), a drawdown distribution, the chance of a 20 % drawdown and the longest losing streak; a bootstrap gives a Sharpe confidence interval; a regime table shows results by year, by QQQ above/below its 200-day average and by volatility tercile; a breadth check runs the same rules on SPY, IWM, DIA and XLK. Finding: most profit comes in high-volatility regimes, and 1 year in 16 lost money. *S4.1, S4.2, S4.3, S4.4*
8. **Freeze.** Allay: *"Freeze it."* The candidate's parameters, code commit and config hash are recorded and can no longer change. *S5.1*

**Session 3 — the honest tests (about 20 minutes)**

9. **Out-of-sample, once.** The frozen candidate runs on 2016–2025. Claude compares it with the expectation band built from the in-sample Monte Carlo, and reports a deflated Sharpe that accounts for the 31 trials. *S5.2, S5.4*
10. **Walk-forward.** Rolling 6-year in-sample / 2-year out-of-sample windows across 2000–2025, re-optimising over the same small grid in each window, give a walk-forward efficiency and a per-window table. *S5.3*
11. **Decide.** The scorecard lists every gate with pass, fail or missing and links to the evidence. Two branches:
    - **All gates pass** → promote. The product computes the expectation band for paper trading (trades, win rate and drawdown expected over 8 weeks). *S6.1, S6.2*
    - **Out-of-sample drawdown falls outside the Monte Carlo 95 % band** → iterate. Allay wants to try a time-stop exit. The change becomes candidate v2 in the same study; the study is marked contaminated because the out-of-sample window has been seen, so v2's evidence must come from walk-forward and paper. *S6.3, S6.4*

**Weeks 2–9 — paper trading**

12. **Hand off to paper.** Claude prepares the exact `live create … --compare-to <run_id>` and `live start` commands; Allay runs them. *S7.1*
13. **Weekly check.** Claude compares the session's trades with the expectation band: inside the band → carry on; outside → flagged with the likely cause (fills, slippage, missed signals, regime). After 8 weeks or 20 trades the scorecard's paper gate is filled in. *S7.2, S7.3*
14. **Record.** Every run, decision and scorecard is exported into the vault (Experiment notes, the Strategy note, the weekly research review). *S8.1, S8.2*

**Later — joining the portfolio** *(phase 5)*

15. **Fit with the other sleeves.** Claude runs the cumulative-RSI sleeve over its scan's universe, checks its correlation with the trend-following sleeve already held, and backtests the combined portfolio with and without it on the same holdout discipline. It joins only if the combined result improves — for example a shallower combined drawdown in high-volatility regimes — even if its standalone numbers are modest. *S9.1–S9.5*

## User stories

Thirty-four stories in nine epics cover the scenario end to end (the phased delivery section says which phase delivers each); each is written for Allay as the researcher, with Claude acting through the MCP on his behalf.

**E1 — Set up a study**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S1.1 | As a researcher, I want to open a study for an idea with its hypothesis, strategy, instruments, parameter space and pass criteria, so that every later run has a home and a question it answers | Study gets an id and a slug matching the vault Idea; parameter space is validated against the strategy's parameter model; pass criteria reference gate ids |
| S1.2 | I want the out-of-sample window locked when the study is created, so that I cannot peek at it while exploring | Split is required; any run, bar export or analysis that touches the locked window before a candidate is frozen is refused with a clear message |
| S1.3 | I want a trial budget and a live trial count, so that I see the cost of each extra sweep | Every in-sample run increments the ledger; submitting past the budget is refused unless I give a reason, which is stored |
| S1.4 | I want to check that a strategy, its parameters and the data exist before running, so that failures happen in seconds, not after a minute of loading | Validation returns the resolved request and data coverage; missing instrument or catalog returns a specific error and the fix |

**E2 — Run and track work**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S2.1 | I want to start a backtest and keep working, so that long runs never block the conversation | Submit returns a job id in < 2 s; the run is persisted with a run id visible in NTrader's history and web UI |
| S2.2 | I want to see progress and cancel a job, so that a wrong run doesn't waste 20 minutes | Status shows phase and log tail; cancel stops the job within 5 s and leaves no half-written run |
| S2.3 | I want every run to record its study, parameters, git commit, dirty state and config hash, so that any result can be reproduced or questioned later | Fields present on every run; a reproduce request re-runs the stored config and matches metrics to stored precision |
| S2.4 | I want a buy-and-hold benchmark and the incumbent strategy run on the same window, so that results are never read in isolation | Benchmark runs use the same metric code and appear beside strategy runs in comparisons |
| S2.5 | I want bar data for the in-sample window to test whether an effect exists before writing strategy code | Export writes a CSV to the vault; requests reaching into the locked window are clamped and say so |

**E3 — Explore in-sample**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S3.1 | I want to run a parameter grid in-sample as one request, so that exploring is fast and every combination is counted | Grid expands to individual jobs; total counted against the budget before anything runs; refused if it would exceed it |
| S3.2 | I want a sensitivity map with a plateau score and a recommended parameter set, so that I pick a stable region instead of a lucky spike | For each metric: a grid of results, neighbour-stability score per cell, the recommended cell (best plateau centre) and the raw best cell, with the gap between them |
| S3.3 | I want to compare runs side by side with the differing parameters highlighted, so that I see what each change did | Up to 20 runs; per-metric best marked; benchmark rows included |

**E4 — Understand variability**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S4.1 | I want a Monte Carlo of a run's trades, so that I see the range of drawdowns, returns and losing streaks I could live through | Bootstrap and reshuffle methods; ≥ 1,000 iterations; 5th / 50th / 95th percentiles of CAGR, max drawdown, longest losing streak, time under water; probability of breaching a drawdown I name |
| S4.2 | I want confidence intervals on key metrics and a sample-size verdict, so that I know whether 40 trades prove anything | Bootstrap intervals for Sharpe, profit factor, win rate; minimum track record length; "insufficient sample" flag below 30 trades |
| S4.3 | I want results split by year and by market regime, so that I see whether the edge depends on one kind of market | By calendar year; by benchmark above/below its 200-day average; by realised-volatility tercile; each split shows trades, return, drawdown, win rate |
| S4.4 | I want the same rules run on related instruments it wasn't tuned on, so that I know the edge isn't specific to one chart | One request runs the frozen or chosen params on a list of symbols; table of results; counted as breadth, not as trials |

**E5 — Validate honestly**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S5.1 | I want to freeze a candidate, so that what I test out-of-sample and on paper is exactly what I explored | Records params, strategy code commit and config hash; frozen candidates are immutable |
| S5.2 | I want to run the out-of-sample test once per candidate, so that the holdout stays honest | Second attempt refused unless an override reason is given; an override marks the study contaminated |
| S5.3 | I want a walk-forward analysis, so that I know the way I choose parameters works on unseen data, not just the parameters I chose | Rolling or anchored windows; optimisation over a declared grid inside each window; per-window table and walk-forward efficiency |
| S5.4 | I want the Sharpe ratio deflated by the number of trials, so that a best-of-many result is judged fairly | Deflated Sharpe and its probability shown with the trial count used |

**E6 — Decide and iterate**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S6.1 | I want a scorecard of a candidate against every gate, so that the promote / iterate / reject call is quick and evidence-based | Each gate: pass, fail or missing, the number behind it and a link to the run or analysis; thresholds read from the vault's gates |
| S6.2 | I want an expectation band for paper trading, so that I know what normal looks like before the first paper trade | For a stated horizon (weeks or trades): ranges for trade count, win rate, average trade, drawdown |
| S6.3 | I want to try a change after a failed check without losing the record of what came before, so that iteration stays honest | New candidate version inside the same study; ledger continues; contamination shown on every later scorecard |
| S6.4 | I want to reject or park a study with a reason, so that the vault remembers dead ends and why | Status and reason stored; rejected studies remain searchable |

**E7 — Paper trade and monitor**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S7.1 | I want the exact commands to start a paper session linked to my candidate, so that I start it myself with no typing errors | Commands include the frozen params and `--compare-to` the candidate's run; the MCP never starts or stops sessions |
| S7.2 | I want my paper results compared with the expectation band each week, so that drift is caught early | Trades, win rate, average trade and drawdown vs band; inside / outside per metric |
| S7.3 | I want drift flagged with its likely cause, so that I know whether the strategy or the execution is off | Flags separate slippage and fills, missed or extra signals, and results outside the band with fills as expected |

**E8 — Keep the record**

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S8.1 | I want every run, analysis and scorecard exported to the vault, so that my research notes hold the raw evidence | JSON + CSV in the vault's results folder, written only to allowed folders |
| S8.2 | I want to list and search studies and runs, so that I can pick up where I left off or answer "what have we tried on momentum?" | Filters by status, strategy, symbol, tag, date; headline metrics only |

**E9 — Build the portfolio** *(phase 5, needs NTrader portfolio support)*

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S9.1 | As a researcher, I want to backtest a strategy over the universe its daily scan selects, so that results reflect how it will actually trade, not one hand-picked chart | Scan rules are part of the study; each day's universe is reproducible from the data; results per symbol and in total |
| S9.2 | I want to see how a candidate sleeve correlates with the sleeves already in the portfolio, so that I add diversification, not more of the same | Return correlation and drawdown overlap against each existing sleeve and the current portfolio |
| S9.3 | I want to backtest the combined portfolio with allocation and risk rules, so that the number that matters — the combined result — is tested like any strategy | Sleeves, weights, total-risk and exposure limits as inputs; combined metrics, per-sleeve contribution, same holdout discipline as single strategies |
| S9.4 | I want the portfolio broken down by market regime, so that I see whether trend and mean-reversion sleeves really offset each other | Regime table for each sleeve and the total, side by side |
| S9.5 | I want a portfolio scorecard and expectation band, so that adding or removing a sleeve is an evidence-based decision | Before/after comparison for the change; gates for the portfolio as a whole |

## Key flows

Four flows carry the scenario; each ends with something written to the vault, and each way backwards is recorded on the study rather than erased.

&#91;embedded content: study states · 5 stages forward, 2 recorded ways back\]

The accented stage is the end of the product's job: going live is Allay's decision, outside the MCP.

**F1 — Run a job** *(S1.3, S2.1–S2.3, S8.1)*

1. Claude submits a run, sweep, benchmark or analysis for a study.
2. The server checks the split (nothing in the locked window), the trial budget and the config, then queues the job and returns its id.
3. A worker runs it natively and persists the run with its study, parameters, commit and config hash.
4. Claude polls the job, reads the result, exports it to the vault and reports the headline in plain language with the benchmark beside it.

**F2 — Freeze and test honestly** *(S3.2, S5.1–S5.4, S6.1, S6.2)*

1. From the sensitivity map, Claude recommends the plateau centre and shows the gap to the raw best cell.
2. Allay freezes it; params, code commit and config hash become immutable.
3. The out-of-sample run executes once; walk-forward and Monte Carlo run on the frozen candidate.
4. The scorecard fills every gate with pass, fail or missing, and the expectation band is computed.

**F3 — Iterate after a failed check** *(S6.3, S6.4, S1.3)*

1. The scorecard shows a fail (for example, out-of-sample drawdown beyond the Monte Carlo 95th percentile).
2. Claude explains the likely cause and the options: reject, park, or a new candidate version.
3. A new version stays in the same study, keeps counting trials, and is marked contaminated if out-of-sample data was already seen — its evidence must then come from walk-forward and paper.
4. Reject or park records the reason; the study stays searchable as a dead end.

**F4 — Weekly paper check** *(S7.1–S7.3)*

1. Allay starts the session himself from the generated commands.
2. Each week Claude reads the session, compares trades, win rate, average trade and drawdown with the expectation band, and files a row in the vault's paper note.
3. Outside the band, Claude flags the likely cause — execution (fills, slippage), signals (missed or extra), or the strategy itself.
4. Stopping or continuing the session is Allay's call, made in the CLI.

## Guardrails against overfitting, and how variability is shown

The product enforces the rules that are cheap to break by accident and reports the rest as numbers on the scorecard; it never blocks Allay outright, but every override is written down where later decisions will see it.

**Enforced by the server**

| Risk | Guardrail | Override |
| --- | --- | --- |
| Peeking at the holdout while exploring | Runs, analyses and bar exports that touch the out-of-sample window are refused until a candidate is frozen | None before freeze |
| Re-testing until the holdout looks good | One out-of-sample run per frozen candidate | Allowed with a reason; marks the study contaminated |
| Endless tuning | Trial budget per study, counted before a sweep starts | Allowed with a reason, stored in the ledger |
| Silent drift between what was tested and what is traded | Frozen candidates are immutable; paper commands are generated from the frozen record | None |
| Results from uncommitted code | Every run records git commit and dirty state; the scorecard shows a warning for dirty runs | Warning only |

**Reported on the scorecard (judged against the vault's gates)**

| Question | Measure | How it's shown |
| --- | --- | --- |
| Is the best result just the luckiest of many tries? | Deflated Sharpe ratio using the study's trial count | Deflated value and probability beside the raw Sharpe |
| Is the chosen setting stable? | Plateau score: median neighbour result ÷ chosen cell's result | Sensitivity grid with the chosen and the raw-best cell marked |
| Does it hold on unseen data? | Out-of-sample result vs the in-sample expectation band; walk-forward efficiency | Inside / outside band; per-window table |
| How bad could it get? | Monte Carlo 95th-percentile drawdown, probability of a named drawdown, longest losing streak, time under water | Percentile table and a distribution chart |
| How sure are we of the headline numbers? | Bootstrap confidence intervals; trade-count adequacy | Interval next to each metric; "insufficient sample" flag |
| Does it depend on one kind of market? | Results by year, trend regime and volatility tercile | Regime table with losing cells highlighted |
| Is it specific to one instrument? | Same rules on related, untuned instruments | Breadth table |

The enforced guardrails and the regime and sample-size rows arrive in phase 2. Rows that need Monte Carlo, parameter sensitivity, walk-forward or deflated Sharpe arrive in phase 4; until then the scorecard shows them as "missing", never as passed.

**Defaults (configurable per study):** Monte Carlo 5,000 iterations with both bootstrap and reshuffle; walk-forward windows sized so each out-of-sample slice holds at least 20 trades; volatility regimes from the benchmark's 21-day realised volatility; percentiles reported as 5th / 50th / 95th.

## Capabilities: MCP tools mapped to stories

Thirty-nine tools deliver the thirty-four stories; every story has at least one tool and every tool serves a story. **Job** tools return a job id at once and run in the background; **study** tools change only study records; **read** tools change nothing.

| Tool | Kind | What it does | Stories | Phase |
| --- | --- | --- | --- | --- |
| `server_info` | read | Versions, git state, catalogs, health, queue depth, limits, available capabilities | — | 1 |
| `list_strategies` · `describe_strategy` | read | Registered strategies; parameter schema, defaults, minimal config | S1.1, S1.4 | 1 |
| `list_catalogs` · `catalog_availability` | read | Catalog contents; bars and instrument coverage for a symbol | S1.4 | 1 |
| `validate_config` | read | Resolve a config and check data coverage without running | S1.4 | 1 |
| `submit_backtest` | job | One run, persisted with full provenance (in a study from phase 2) | S2.1, S2.3 | 1 |
| `get_job` · `list_jobs` · `cancel_job` | read / job | Progress, log tail, cancel | S2.2 | 1 |
| `get_run` | read | Run detail and metrics with units | S3.3 | 1 |
| `compare_runs` | read | Side-by-side table, best per metric | S3.3 | 1 |
| `export_results` | read | Runs, analyses, scorecards → JSON + CSV in an allowed vault folder | S8.1 | 1 |
| `create_study` | study | Hypothesis, strategy, instruments, parameter space, split (locks out-of-sample), pass criteria, trial budget | S1.1–S1.3 | 2 |
| `get_study` · `list_studies` · `search_runs` | read | Study with ledger, candidates, contamination; study and run search | S1.3, S8.2 | 2 |
| `update_study` | study | Status (rejected / parked) or budget extension, each with a reason | S1.3, S6.4 | 2 |
| `submit_benchmark` · `reproduce_run` | job | Buy-and-hold on the same window and metric code; re-run a stored config | S2.3, S2.4 | 2 |
| `export_bars` | read | In-sample bars to CSV for concept probes (clamped to the split) | S2.5 | 2 |
| `get_trades` · `get_equity_curve` | read | Paginated trades; downsampled equity and drawdown | S3.3 | 2 |
| `get_regime_breakdown` | read | Results by year, trend regime, volatility tercile, from stored trades | S4.3 | 2 |
| `freeze_candidate` · `new_candidate_version` | study | Immutable candidate (params, commit, config hash); changed version in the same study, contamination tracked | S5.1, S6.3 | 2 |
| `run_out_of_sample` | job | The one out-of-sample run per candidate | S5.2 | 2 |
| `get_scorecard` | read | Gates with pass / fail / missing; expectation band (upgraded in phase 4) | S6.1, S6.2 | 2 |
| `paper_commands` | read | `live create` / `live start` commands for a frozen candidate, as text | S7.1 | 3 |
| `list_sessions` · `get_session` | read | Paper session state and trades beside the expectation band, with drift flags | S7.2, S7.3 | 3 |
| `submit_sweep` · `run_sensitivity` | job / read | Parameter grid as counted jobs; plateau score, recommended vs raw-best cell | S3.1, S3.2 | 4 |
| `run_monte_carlo` | job | Trade bootstrap and reshuffle; percentiles, breach probability, confidence intervals | S4.1, S4.2 | 4 |
| `submit_breadth` | job | Same params on untuned symbols | S4.4 | 4 |
| `submit_walk_forward` | job | Rolling or anchored windows with in-window optimisation; deflated Sharpe on the scorecard | S5.3, S5.4 | 4 |
| `create_portfolio_study` · `submit_portfolio_backtest` | study / job | Sleeves, weights, risk limits, scan-driven universes; combined backtest under the same holdout rules | S9.1, S9.3, S9.5 | 5 |
| `get_sleeve_correlation` | read | Correlation, drawdown overlap and regime offsets between sleeves | S9.2, S9.4 | 5 |

Not provided, on purpose: placing or cancelling orders, starting, stopping or creating sessions, reconciling with the broker, importing or fetching data.

## Phased delivery

Start with a walking skeleton that runs one backtest end to end through the MCP, then add one working increment per phase; phases 1–3 need nothing new from NTrader, and phases 4–5 switch on as the engine gains the features they wrap.

&#91;embedded content: phased delivery · 6 phases, 2 engine dependencies\]

The green row is in use today; the blue row is the next build.

| Phase | Adds | What works end to end afterwards | Stories | NTrader dependency |
| --- | --- | --- | --- | --- |
| 0 · Vault and queue (done) | Trading Research vault, research skill, `Lab/queue` runner | Scenario steps 1, 4, 5 by hand: Claude writes configs, the queue runs them on the Mac, Claude files the results | — | None |
| 1 · Walking skeleton | MCP server on stdio; `server_info`, `list_strategies`, `describe_strategy`, `catalog_availability`, `validate_config`, `submit_backtest`, `get_job`, `get_run`, `compare_runs`, `export_results`; one worker at a time | Steps 4–5 from chat with no queue: validate, run, compare with a buy-and-hold config, file to the vault | S1.4, S2.1, S2.2, S2.3, S3.3, S8.1 | None |
| 2 · Honest research loop | Studies with locked holdout and trial ledger, `submit_benchmark`, `export_bars`, `get_trades`, `get_equity_curve`, `get_regime_breakdown` (by year, trend, volatility — computed from stored trades), `freeze_candidate`, `run_out_of_sample`, `get_scorecard` for the gates computable without phase 4, study status changes | Steps 2–5, 7 (regimes only), 8, 9, 11: a single strategy goes from idea to a frozen, out-of-sample-tested candidate with a scorecard; manual tuning is counted | S1.1–S1.3, S2.4, S2.5, S4.3, S5.1, S5.2, S6.1, S6.3, S6.4, S8.2 | None |
| 3 · Paper loop | `paper_commands`, `list_sessions`, `get_session`; expectation band from the backtest's own per-week and per-trade statistics | Steps 12–14: validated candidate → paper session Allay starts → weekly checks with drift flags | S6.2, S7.1–S7.3 | None (paper sessions exist) |
| 4 · Robustness | `submit_sweep`, `run_sensitivity`, `run_monte_carlo`, `submit_walk_forward`, `submit_breadth`, deflated Sharpe; scorecard and expectation band upgraded to use them | Steps 6, 7 (full), 10: tuning on plateaus, ranges of outcomes, walk-forward before freeze-and-test | S3.1, S3.2, S4.1, S4.2, S4.4, S5.3, S5.4 | Hyperparameter sweeps, Monte Carlo, walk-forward (on the NTrader roadmap) |
| 5 · Portfolio and scans | Portfolio studies, combined backtests, correlation and regime-balance views, portfolio scorecard and expectation band, scan-driven universes | Step 15: a validated sleeve is tested inside the portfolio; the combined result decides | S9.1–S9.5 | Multi-strategy portfolio backtests; scanner/universe selection |

**How phase 4 tools arrive.** Their contracts (inputs, outputs, errors) are defined in this document now, so Claude's research skill can be written against them. Each tool is registered only when its NTrader feature exists; `server_info` lists available capabilities, and the skill falls back to the phase-2 path (single runs, regime splits) when a capability is missing. Parameter sweeps could also come earlier as plain orchestration of single runs if waiting on the engine becomes the bottleneck.

**Exit check for every phase.** The phase is done when the scenario steps in its row run from a Claude chat on the Mac, results land in the vault, and the safety tests (no live imports, pinned tool set, clean stdout) pass.

**Open questions**

- ~~Where should studies live?~~ **Resolved (phase 2):** Postgres, in the `research_studies`, `research_trials`, `research_candidates` and `research_study_events` tables.
- ~~Should the server read gate thresholds from the vault or keep its own copy?~~ **Resolved (phase 2):** from the vault only, through a fenced `yaml ntrader-gates` block in `System/Gates.md`; a missing block or key makes the affected rows "missing", never "pass".
- Which asset types come first after US stocks and ETFs — crypto (Kraken data already exists in NTrader) or futures?
- Are daily scans part of NTrader (shared by research and live trading) or a separate tool feeding both? This decides where phase 5's scan stories live.
- Monte Carlo on trades ignores serial correlation; is a block bootstrap on daily returns needed in phase 4 or later?

## Technical approach

The MCP is a thin local server in the NTrader repo (`src/mcp_server/`, official Python MCP SDK, stdio) that Claude Desktop launches on the Mac; it reuses NTrader's request resolution, data loading, orchestrator, persistence and query services, and adds only studies, jobs and the analyses above.

- **Native on the Mac.** Postgres, the Parquet catalogs and instrument metadata are only reachable there; Claude's own shell is a sandboxed VM that cannot run backtests.
- **One worker process per job.** BacktestEngine is single-use, Nautilus' log guard panics on re-initialisation and its output would corrupt the protocol stream, so every backtest runs in a fresh child process on the same code path as `backtest run`. Analyses that only read stored trades (sensitivity, regimes, scorecard) run in the server.
- **Fail fast on missing data.** Runs never fall back to an IBKR fetch or a fake test instrument; they return a specific error and the fix.
- **Safety boundary, tested.** The package imports nothing from NTrader's live-trading modules; the registered tool set is pinned by a test; paper-session reads are plain SELECTs; file writes go only to allow-listed vault folders.
- **Delivery discipline.** TDD across the repo's unit, component, integration and end-to-end tiers; an end-to-end test drives the server through a real MCP client and checks that stdout carries only protocol messages.

* **Capabilities grow with the engine.** Each tool is registered when the NTrader feature behind it exists; `server_info` reports which capabilities are live so the research skill can choose the best available path. Phase 4 and 5 tools wrap NTrader features rather than re-implementing them in the server, so the CLI, web UI and MCP always agree.

&#91;embedded content: ntrader-mcp architecture · server, workers, stores\]
