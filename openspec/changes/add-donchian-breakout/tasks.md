# Tasks

Strategy work happens in the submodule (`src/core/strategies/custom/`, repo
`Trading-ntrader-strategies`) on a branch; its tests run from NTrader
(`uv run pytest src/core/strategies/custom/tests`). Each task starts with the failing test.

## 1. Strategy rules (submodule)

- [ ] 1.1 Unit (failing first) `tests/test_donchian_breakout_params.py`: the parameter model's
      defaults (55/20/20/2/1/100/false) and its refusal of `exit_lookback >= entry_lookback`
      naming both fields; verify the test fails, then add `DonchianBreakoutParameters` to
      `models.py` and verify it passes.
- [ ] 1.2 Component (failing first) `tests/test_donchian_breakout_signals.py`, driving the
      strategy with synthetic daily bars through a test engine (copy the
      `test_riptide_crypto_engine.py` harness): long breakout submits one BUY filling at the
      signal bar's close; the signal bar's own high is not in the channel; nothing before the
      channel and ATR are full; short breakout ignored with `allow_short` off and taken with it
      on. Verify failing, implement entries in `donchian_breakout.py`, verify passing.
- [ ] 1.3 Component (failing first) in the same file: long and short channel exits, the
      close-checked ATR stop fixed at entry (100.00, ATR 2.00, mult 2, close 95.90 exits), no
      resting stop order ever submitted, no reversal on an exit bar, no order from
      `on_stop`/`on_dispose` with a position open. Verify failing, implement, verify passing.
- [ ] 1.4 Verify the strategy registers: `uv run python -m src.cli.main strategy list` (or the
      registry test helper) shows `donchian_breakout`, and the AST lifecycle-hook scan
      `tests/unit/core/test_live_stop_path_is_inert.py` still passes.

## 2. Sizing per asset class (submodule)

- [ ] 2.1 Unit (failing first) `tests/test_donchian_breakout_sizing.py`: equity risk size
      (1,000,000 x 1% / 4.00 = 2,500 shares), the `max_position_pct` cap, futures floored to
      whole contracts (0 means no order) and fixed `contracts`, FX base units priced in USD
      (EURUSD and USDJPY), crypto fractional coins capped at free cash and 0 under Kraken's
      minimum, fixed `units`. Verify failing, implement `_position_size` over
      `_sizing.risk_quantity` plus the cap, verify passing.
- [ ] 2.2 Component (failing first) `tests/test_donchian_breakout_crypto.py`: on BTC/USD with
      `allow_short` on, a short breakout submits nothing, logs the disabled-shorts warning
      once, and the run has no `CRYPTO_SPOT_NO_SHORT` denial. Verify failing, implement,
      verify passing.
- [ ] 2.3 Add the strategy to the submodule `README.md` table and a short section on its
      close-execution model (design D3/D4); verify `make lint` and the size-cap guard
      (`tests/unit/governance/test_size_caps.py`) pass with the submodule checked out.

## 3. NTrader wiring

- [ ] 3.1 Open the submodule PR, get it reviewed and merged; verify the merge commit on
      `Trading-ntrader-strategies` main.
- [ ] 3.2 NTrader branch: bump the submodule pin to that merge and add
      `configs/donchian_breakout.yaml` (AAPL 1-day, defaults, comments naming D3); verify
      `uv run python -m src.cli.main backtest run configs/donchian_breakout.yaml --catalog
      firstrate-stocks --no-persist` completes with trades.
- [ ] 3.3 Smoke one run per asset class with defaults, `--no-persist`, 2010-2021 (crypto
      2014-2021): AAPL (`firstrate-stocks`), SPY (`firstrate-etf`), CL with `allow_short`
      (`firstrate-futures`), EURUSD with `allow_short` (`firstrate-fx`), BTC
      (`firstrate-crypto`); verify each completes, sizes in the right unit, and record trade
      counts in `docs/agent/donchian-breakout.md` (new, short: rules, defaults, smoke table).
- [ ] 3.4 Run `make test-unit`, `make test-component`, `make lint`, `make typecheck`; open the
      NTrader PR and get it merged.

## 4. Studies (research server, after 3.4 is merged)

- [ ] 4.1 Verify the research server sees the strategy: `list_strategies`,
      `describe_strategy("donchian_breakout")`, and `validate_config` for each lead on its
      catalog and windows (design D9).
- [ ] 4.2 Create the five studies (`create_study`, design D9 windows, grid, trial budget 30,
      hypothesis stating D3 and, for SPY, the seen holdout); verify `list_studies` shows five
      with locked OOS windows.
- [ ] 4.3 Per study: submit the 18 grid trials and the in-sample buy-and-hold benchmark; verify
      every job finished (`list_jobs`) and the study ledger has 18 trials.
- [ ] 4.4 Per study: pick the candidate by the D9 rule, `freeze_candidate`, `run_out_of_sample`
      once, `submit_benchmark` on the OOS window; verify each study has exactly one OOS run.
- [ ] 4.5 Per study: cost stress (2x), neighbourhood sweep + `run_sensitivity`,
      `run_monte_carlo`, breadth on the basket for both windows, walk-forward; verify
      `get_scorecard` shows no G0-G3 row `missing` except where design Risks says why.
- [ ] 4.6 Per study: `run_portfolio_backtest` for the basket view; verify a 'portfolio'
      analysis is stored (`get_analysis(kind='portfolio')`).
- [ ] 4.7 Optional crypto cross-check: one unattributed BTC run of the frozen parameters on
      `kraken-crypto`; record it beside the crypto row.

## 5. Cross-market scorecard

- [ ] 5.1 `export_results(study=...)` for each study; verify five study notes in the vault.
- [ ] 5.2 Write the cross-market scorecard note (`Experiments/`, design D9 columns, D3 stated
      on every row, inconclusive where trades < 30); verify every number in it traces to a
      `get_scorecard` / `get_analysis` result, and hand the study status decisions to Allay.

## Workflow follow-up

- Archive the change with `/openspec-archive-change` once both PRs are merged and the
  scorecard is filed, so `openspec/specs/donchian-breakout-strategy/` matches the code.
