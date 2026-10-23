# E2E Research Journey — QA Plan

2026-10-21 · Owner: Allay · Status: draft, not yet run

## 1. Why this exists

NTrader now covers the whole path from idea to broker: FirstRate stock and ETF catalogs, the
backtest engine, paper trading against IBKR, and the research MCP (studies, holdout, robustness,
scorecard, paper handoff). Each piece was built and verified on its own, story by story. **Nobody
has yet walked one strategy through all of it, start to finish, as a researcher would.**

This plan does that walk, as a QA exercise with three goals, in this order:

1. **Prove the happy path end to end.** One strategy found online → studied → backtested →
   frozen → out-of-sample → paper traded during market hours → live results compared against the
   backtest. Every hand-off between components is a test case.
2. **Learn the system by using it.** Allay drives; Claude explains each step, what the numbers
   mean, and which component is doing the work. The run doubles as onboarding.
3. **Harvest repeatable skills.** Every step done by hand once becomes a candidate skill, written
   from what actually happened, not from what we expected to happen.

Happy path first. Failure paths (disconnects, kill -9, rejections, splits) were already proven by
the Phase 3 procedures P1–P24 in `docs/qa/phase3-live-verification.md` and are out of scope here
unless the happy path trips one.

## 2. Scope

**In scope**

- Research through the MCP, from Claude Code and from Claude Desktop (both are first-class).
- The Trading Research vault as the record (`System/Conventions.md`, `Gates.md`,
  `strategy-research.SKILL.md`).
- CLI: `backtest`, `data`, `catalog`, `strategy`, `live check|create|start|status|list|reconcile|seal`.
- Web UI as a viewer of results (backtest list/detail, explorer charts).
- IBKR **paper** account, during regular trading hours (09:30–16:00 ET).
- Comparing paper results against the backtest at three levels (section 6).

**Out of scope**

- Real-money live trading. This plan ends at a decision about it, not at doing it.
- Multi-strategy / portfolio (Phase 5b, not built).
- New features. Gaps found become defects or backlog items, not mid-run code changes, unless a
  defect blocks the happy path outright.

## 3. The journey at a glance

| Stage | What happens | Where | Who acts | Time |
| --- | --- | --- | --- | --- |
| S0 Preflight | Environment healthy, data current, gateway reachable | CLI, scripts | Claude (checks), Allay (fixes `.env`, Gateway) | 20 min |
| S1 Capture | Find a strategy online, file Source + Idea | Vault | Claude | 20 min |
| S2 Study | Hypothesis, split, budget, pass criteria; holdout locked | MCP | Claude proposes, Allay says go | 10 min |
| S3 Build | Strategy file written, registered, validated | Repo (custom/), MCP | Claude writes, Allay reviews | 30–60 min |
| S4 Baseline | First in-sample run + buy-and-hold + incumbent | MCP, web UI | Claude | 15 min |
| S5 Explore | Sweep, sensitivity, Monte Carlo, regimes, breadth | MCP | Claude | 30–45 min |
| S6 Validate | Freeze → OOS once → walk-forward → cost stress → scorecard | MCP | Allay freezes, Claude runs | 30 min |
| S7 Paper | Generated commands → session running in RTH | CLI | **Allay runs**, Claude watches | 1 RTH day, then weeks |
| S8 Compare | Data, decision and distribution parity vs backtest | CLI, MCP | Claude | 30 min / check |
| S9 Decide | Continue / stop / promote; vault records it | Vault | Allay | 10 min |

Guardrails stay on throughout: the MCP never creates, starts or stops sessions and never places
orders. Allay types every `live create` / `live start`.

## 4. Choosing the strategy

The strategy is picked in TC-S1-01, but it has to satisfy constraints the plan depends on.

**Selection criteria**

- Published online with explicit rules (entry, exit, sizing), so the build step is translation,
  not invention.
- Long-only, single instrument, no shorting, no options — keeps the paper account simple.
- Instruments already in the catalogs (`firstrate-stocks` or `firstrate-etf`) and tradable on
  IBKR paper.
- Enough trades: ≥ 30 in-sample (Conventions), and — the binding one — **enough in paper to
  compare against within the time we have**.

**That last point forces a two-track design.** A daily-bar strategy trades perhaps once a week;
its paper results take 8 weeks and 20 trades before G4 judges anything. That is the real research
path and we run it, but it gives no feedback during the first market day. So:

| Track | Purpose | Bars | Example shape | Paper horizon |
| --- | --- | --- | --- | --- |
| **A — Real research** | The strategy Allay actually cares about; full honest loop through G4 | 1-DAY | IBS / RSI(2)-style mean reversion on an ETF (SPY, QQQ, IWM) | 8 weeks + 20 trades |
| **B — Fast loop** | Exercise paper → seal → compare in one RTH day; learn the mechanics | 1-MINUTE | A published intraday rule (e.g. opening-range breakout) on a liquid stock | 1 RTH day |

Track B is not a strategy we expect to keep. It is a probe of the plumbing that happens to be a
real published rule, so the comparison steps have data to chew on before Track A has any.
Both tracks go through S1–S8; Track B may skip S5's depth (one sweep, one Monte Carlo) because its
job is the paper-and-compare half.

**Candidate shortlist** (final pick is TC-S1-01, from what Allay finds):

- *Track A:* Internal Bar Strength (IBS) mean reversion on QQQ/SPY; Connors' cumulative RSI is
  already studied (`crsi-qqq-*`), so pick something new or deliberately re-run it as a control.
- *Track B:* 15- or 30-minute opening-range breakout with a time stop before close; a VWAP
  reversion rule. Must flatten by end of day, so nothing is carried overnight.

## 5. Test cases

Each case is written as the researcher's step. **Expected** is the happy-path result; anything
else is logged as a finding (section 8). **Learn** is what Allay should be able to explain after
the step. **Skill?** marks steps to harvest into a skill (section 7).

### S0 — Preflight

**TC-S0-01 · Services are up and the schema is current**
- Steps: Postgres (Homebrew pg17) and Redis running; `alembic upgrade head`;
  `scripts/check_alembic_version.py`.
- Expected: single head `dc41856246ad`; no pending migrations.
- Learn: why `alembic upgrade head` is load-bearing (P11: `live list` failed on a missing column).
- Skill? yes — preflight.

**TC-S0-02 · Configuration traps are cleared**
- Steps: inspect `.env` for `TRADE_SIZE` (and any other `_settings_map` field); `describe_strategy`
  / `validate_config` on a known strategy and read the warnings.
- Expected: `TRADE_SIZE` unset or deliberately chosen; no surprise override warnings.
- Learn: a global setting always beats a strategy's schema default (the 1M-share trap).
- Skill? yes — preflight.

**TC-S0-03 · Catalog covers the windows we'll need**
- Steps: `list_catalogs`; `catalog_availability` for the chosen instruments and timeframes.
- Expected: coverage start/end known for each; the end date is recorded because it decides the
  OOS window and whether recent bars must be fetched from IBKR for S8.
- Learn: FirstRate history ends where the import ended; paper days after it are not in the catalog.
- Skill? yes — preflight.

**TC-S0-04 · MCP server is reachable from both clients**
- Steps: `server_info` from Claude Code and from Claude Desktop.
- Expected: same git commit, clean (or knowingly dirty) tree, capabilities include phase 4 tools,
  queue depth 0.
- Learn: Desktop launches the server twice and they share one job queue — that is normal.

**TC-S0-05 · Broker path works before anything trades** *(RTH day only)*
- Steps: IB Gateway (paper) logged in; `live check --observe-seconds 0`; then the bars preflight
  `scripts/diagnostics/live_bars_probe.py --bar-type <instrument>-1-MINUTE-LAST-EXTERNAL
  --run-seconds 150`.
- Expected: `live check` exit 0 against the paper account; probe returns `bars=3`.
- Learn: exit codes (3 = gate refused, 4 = broker unreachable); error 162 means a competing login.
- Skill? yes — paper launch.

**TC-S0-06 · Account is flat**
- Steps: IBKR connector read tools (positions, open orders) or TWS.
- Expected: no positions or working orders in the instruments under test.
- Learn: a session only owns what it opened; leftovers confuse reconcile and the comparison.

### S1 — Capture

**TC-S1-01 · Pick a strategy from an online source**
- Steps: Allay shares a link (or asks Claude to scout per the skill's *Scout* mode, then picks).
- Expected: rules are explicit enough to code; Claude flags credibility issues (no costs, no OOS,
  survivorship, tiny sample) in 1–3 bullets.
- Learn: what makes a published result untrustworthy before any code runs.

**TC-S1-02 · File it in the vault**
- Steps: Claude creates the `Sources/` note and one `Ideas/` note per testable claim.
- Expected: frontmatter, tags and concept links per `System/Conventions.md` and `Tags.md`;
  `status: inbox`.
- Learn: the vault's lifecycle (inbox → queued → testing → promoted / rejected).

### S2 — Study

**TC-S2-01 · Open the study and lock the holdout**
- Steps: Claude proposes hypothesis, instrument(s), parameter space, IS/OOS split, pass criteria
  (gate ids), trial budget; Allay approves; `create_study`.
- Expected: study id and slug match the Idea; parameter space validates against the strategy's
  parameter model; split recorded.
- Learn: why the split is chosen *before* the first run.

**TC-S2-02 · The holdout is actually locked**
- Steps: deliberately request `export_bars` or a run reaching into the OOS window.
- Expected: refused (runs) or clamped with a message (bar export). *This is the one negative test
  kept on the happy path* — it proves the main guardrail before we rely on it.
- Learn: what the server enforces versus what the scorecard merely reports.

**TC-S2-03 · Probe the concept (optional)**
- Steps: in-sample bars only; forward returns after the signal vs all days, by sub-period.
- Expected: an effect worth building, or a cheap reason to stop here.
- Learn: a probe costs no trial; a backtest does.

### S3 — Build

**TC-S3-01 · Write the strategy**
- Steps: Claude writes a new file in `src/core/strategies/custom/` (submodule) following the
  `strategy-development` skill, test first; walks Allay through it.
- Expected: one file with config + `register_strategy()`; unit test green; rules match the source
  line by line (Claude shows the mapping).
- Learn: `on_bar()` flow, warm-up, Nautilus indicator ranges (RSI is 0–1).

**TC-S3-02 · Registered, valid, data present**
- Steps: `list_strategies`, `describe_strategy`, `validate_config` for the study's instrument and
  IS window.
- Expected: resolved request + coverage returned in seconds; no warnings Allay can't explain.

**TC-S3-03 · Commit before evidence**
- Steps: commit the strategy in the submodule (and the pointer in the main repo) before S4.
- Expected: subsequent runs record a clean commit; scorecard carries no dirty-tree warning.
- Learn: provenance — a run is evidence only if its code can be recovered.

### S4 — Baseline

**TC-S4-01 · First run, benchmark and incumbent together**
- Steps: `submit_backtest` (source's default params, in study), `submit_benchmark`
  (buy-and-hold, same window), and an incumbent strategy if one exists; poll with `get_job`.
- Expected: job ids return in < 2 s; runs persist with study, params, commit, config hash; trial
  ledger reads 1 of N; `compare_runs` shows them side by side.
- Learn: reading the headline metrics against the benchmark, not in isolation.

**TC-S4-02 · The same run in the CLI and web UI**
- Steps: find the run in `backtest history` / `show`, then in the web UI's backtest detail and
  the explorer chart for the instrument.
- Expected: same metrics in all three surfaces; trades plotted on the chart where expected.
- Learn: MCP, CLI and UI read one database — any disagreement is a defect.

**TC-S4-03 · Reproduce**
- Steps: `reproduce_run` on the baseline.
- Expected: metrics match to stored precision.

**TC-S4-04 · File results**
- Steps: `export_results`; Claude fills the Experiment note per the skill's *File results* mode.
- Expected: `Lab/results/<slug>.json` + trades CSV; Experiment note with verdict vs pass criteria.

### S5 — Explore and measure variability (in-sample only)

**TC-S5-01 · Sweep and pick a plateau**
- Steps: `submit_sweep` within budget; `run_sensitivity`.
- Expected: budget checked before anything runs; recommended (plateau) cell vs raw best, with the
  gap; ledger updated.
- Learn: why the plateau centre beats the spike.

**TC-S5-02 · Range of outcomes**
- Steps: `run_monte_carlo` on the chosen cell; `get_regime_breakdown`.
- Expected: 5th/50th/95th percentiles for CAGR, drawdown, losing streak; bootstrap intervals;
  "insufficient sample" flag if < 30 trades; per-year and per-regime table.
- Learn: how bad the next drawdown could plausibly be.

**TC-S5-03 · Breadth** *(may run after freeze)*
- Steps: `submit_breadth` on untuned related instruments.
- Expected: counted as breadth, not trials; results table.

### S6 — Validate honestly

**TC-S6-01 · Freeze**
- Steps: Allay says "freeze it"; `freeze_candidate` (with a benchmark rationale if G1 needs one).
- Expected: params, code commit and config hash recorded and immutable.

**TC-S6-02 · Out-of-sample, once**
- Steps: `run_out_of_sample`; then try it a second time without an override.
- Expected: first run completes; the second is refused. Result compared against the in-sample band;
  deflated Sharpe reported with the trial count.

**TC-S6-03 · Walk-forward and cost stress**
- Steps: `submit_walk_forward`, `submit_cost_stress` (2×).
- Expected: per-window table and walk-forward efficiency; net metrics under doubled costs.

**TC-S6-04 · Scorecard and decision**
- Steps: `get_scorecard`.
- Expected: every gate G0–G4 shows pass / fail / missing with its evidence; G4 missing (no paper
  yet); expectation band present.
- Decision: **for this QA run, Track A proceeds to paper even if a gate fails**, recorded as an
  override in the vault — the goal is to exercise the path. Track B proceeds regardless.
- Learn: promote / iterate / reject, and what contamination means for a v2.

### S7 — Paper trading (market hours)

**TC-S7-01 · Generate the commands**
- Steps: `paper_commands(study)`.
- Expected: exact `live check`, `live create --name … --strategy … --bar-type … --param k=v …
  --compare-to <oos_run_id>`, `live start …`; the band; every warning relayed (failing gates,
  contamination, code drift, `.env` overrides). Claude creates `Paper/<session>.md`.
- Learn: why `--compare-to` is the OOS run, and what each `--param` pins.

**TC-S7-02 · Allay creates and starts the session**
- Steps: S0-05 and S0-06 pass first; Allay runs `live create` then `live start` in a terminal left
  open.
- Expected: phase log reaches `trading`; `live status` from a second terminal shows the session
  running with a fresh heartbeat and bars arriving.
- Learn: session ≠ process run; phases (`gate:account` → `trading`); where the logs go.

**TC-S7-03 · Watch the first trade** *(Track B, same day)*
- Steps: Claude tails the log (ANSI stripped) for `order.*` and `trade.*` records; `live status`.
- Expected: order submitted → accepted → filled; `trade.persisted`; `closed trades` increments on
  the exit. A second `trade.aggregated` that is `persist_skipped reason=reconciliation_owned` is
  the known phantom — expected, not a defect.
- Learn: the order lifecycle as the system records it.

**TC-S7-04 · Reconcile mid-session**
- Steps: `live reconcile <session>`.
- Expected: exit 0 — the local view (Redis engine cache) agrees with the broker.

**TC-S7-05 · Clean stop and seal**
- Steps: Track B: let the time stop flatten before close, then a single Ctrl-C; confirm flat;
  `live seal <session> --starting-capital <paper balance>`.
  Track A: keeps running across days (restart each morning if not run unattended); sealed only at
  the end of the paper horizon.
- Expected: exit 0 on stop; seal creates a paper run linked to the session with trades and
  metrics; no `--force` needed because the session is flat.
- Learn: what sealing freezes and why open positions need `--force`.

**TC-S7-06 · Paper session visible everywhere**
- Steps: `list_sessions`, `get_session`; the sealed run in `backtest history` and the web UI.
- Expected: same trades and counts in each surface.

### S8 — Compare live against the backtest

See section 6 for the method. Each level is its own test case.

**TC-S8-01 · L1 data parity** — live bars vs catalog bars for the same minutes/days.
**TC-S8-02 · L2 decision parity** — replay the paper window as a backtest and diff the trades.
**TC-S8-03 · L3 distribution** — `get_session` against the expectation band, weekly (Track A).

### S9 — Decide and record

**TC-S9-01 · Weekly paper check** *(Track A, every week)*
- Steps: `get_session`, `export_results(session=…)`, row added to `## Checks` in the Paper note.
- Expected: per-metric below / inside / above band; drift flags with cause; G4 progress.

**TC-S9-02 · Close-out**
- Steps: at G4 (8 weeks and 20 trades) or on a decision to stop: seal, final scorecard, Strategy
  note updated, journey retro (section 8).
- Expected: the vault tells the whole story from Source to verdict without the chat history.

## 6. Comparing live against the backtest

"Does paper match the backtest?" has three different answers, and mixing them up is the usual
mistake. We test each separately.

| Level | Question | Method | Pass (happy path) | Available |
| --- | --- | --- | --- | --- |
| **L1 Data** | Did the strategy see the same bars the backtest would? | Fetch the paper days' bars from IBKR into a scratch catalog (or compare against the session's logged bars); diff OHLC per bar | Sub-cent average OHLC Δ; identical timestamps (IB stamps a bar with its **open**) | Day 1 |
| **L2 Decision** | Given those bars, did live make the same decisions? | Backtest the frozen candidate over exactly the paper window on the IBKR-sourced bars; diff entries/exits by time, side, quantity; diff fill prices separately | Same trade count and timestamps; fill Δ explained by spread/slippage. Daily: 0 trade Δ. 1-minute: small Δ allowed (memory: ~0.8% trade Δ between feeds) | Day 1 (Track B), weekly (Track A) |
| **L3 Distribution** | Is paper performance within what the backtest predicts? | `get_session` vs the band from the OOS run; G4 at horizon | Every metric inside the band, or outside with an explained cause | After enough trades |

Notes:

- L2 needs warm-up: the replay must start early enough that indicators are warm at the session's
  first bar, the same way the live strategy warmed up (Story 4.4).
- L2 is the strongest test of the code path and the one the MCP **does not yet automate** (no
  "replay this session" tool). Doing it by hand once is the input to a skill and possibly a tool.
- Slippage is "not measurable" in L3 by design (no decision prices recorded). L2 is where fill
  quality becomes visible.
- `backtest compare <oos_run> <sealed_paper_run>` gives a side-by-side table, but it compares runs
  over different windows — read it as context, never as a parity verdict.

## 7. Skills to harvest

Write each skill **after** its step has been done by hand once, from the transcript of what
actually worked. Extend the vault's `strategy-research` skill where the step is research; put
NTrader-operational steps in repo skills under `.claude/skills/`.

| Candidate | Covers | Home | Built from |
| --- | --- | --- | --- |
| `e2e-preflight` | S0: services, migrations, `.env` traps, catalog coverage, MCP health; RTH adds Gateway, `live check`, bars probe, flat account | repo | TC-S0-* |
| `paper-launch` | Turns `paper_commands` output into a checklist Allay runs; verifies `trading` phase and first bars | repo | TC-S7-01..02 |
| `paper-watch` | Tails logs (ANSI stripped), summarises order/trade records, knows the benign lines (phantom aggregate, cancel-form 162) | repo | TC-S7-03..04 |
| `live-vs-backtest` | L1 + L2: fetch window bars, replay the frozen candidate with warm-up, diff trades and fills, write a parity report | repo (+ MCP tool later) | TC-S8-01..02 |
| `research-journey` | Thin orchestrator: where am I in S0–S9 for a study, what's next | vault skill extension | whole run |

Skill acceptance: the second strategy (or Track A's second week) uses the skill instead of the
plan, and needs no step the skill doesn't cover.

## 8. Recording results and findings

- **Run log:** `docs/qa/e2e-research-journey-log.md` — one row per test case per track: date,
  result (pass / fail / blocked / skipped), evidence (run id, session name, job id, file path),
  notes. Created on the first run day.
- **Findings:** each non-pass gets an id `E2E-<n>`, a severity, and a home:
  - *Blocker* — stops the happy path. Fix now, test first, small commit; re-run the case.
  - *Defect* — wrong but survivable. Into the active `deferred-work.md` or a backlog item.
  - *Friction* — works, but confusing or slow. Usually a skill or doc fix.
  - *Learning* — something Allay didn't know. Goes into the vault (`#lesson/…`) or a Metrics note.
- **Retro** at the end of Track B's day and at Track A's close-out: what broke, what was slow,
  which skills are now worth writing.

## 9. Schedule

| When | What |
| --- | --- |
| Session 1 (any time) | S0-01..04, S1, S2, S3 for both tracks |
| Session 2 (any time) | S4–S6 for both tracks |
| **Day 1 — RTH** | 09:00 ET S0-05/06 · 09:30 start Track A and Track B sessions · watch Track B's first trades · ~15:45 Track B flattens, stop, seal · after close: TC-S8-01/02 for Track B |
| Weekly, weeks 1–8 | Track A: TC-S9-01, plus L2 on the week's trades |
| Close-out | Track A seal, final scorecard, retro, decision on real-money live |

## 10. Exit criteria

The plan is done when:

1. Both tracks have passed S0–S8, with every finding logged and every blocker fixed.
2. L2 decision parity has been shown for at least one paper day on each track.
3. Track A has a G4 verdict on the scorecard, or a recorded decision to stop early.
4. At least `e2e-preflight` and `live-vs-backtest` exist as skills and have been used once without
   this document.
5. Allay can explain, unaided, each stage's purpose and the three levels of live-vs-backtest
   comparison.

## 11. Open questions for Allay

1. **Track B instrument and rule** — fine to trade a 1-minute rule on a liquid stock like NVDA
   (the instrument P11/P24 used), or prefer an ETF so both tracks share an asset class?
2. **Track A while unattended** — leave the node running across days (P6 proved a full RTH day,
   not multi-day), or restart each morning? Restarting exercises resume (Story 4.5) every day.
3. **Gate override for QA** — agree that Track A goes to paper even with a failing gate, recorded
   as an override?
4. **Recent bars for L1/L2** — fetch the paper days from IBKR into a scratch catalog (keeps
   FirstRate catalogs untouched), or into the main catalog?
5. **Which client leads** — run research from Claude Desktop (voice, the intended daily surface)
   or Claude Code (sees the repo and terminal)? Suggest: Desktop for S1–S6 and weekly checks,
   Code for S0, S3 and S7–S8.
