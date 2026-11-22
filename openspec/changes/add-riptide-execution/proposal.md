# Proposal: Riptide execution: next-bar-open exits, gap-aware stops, slippage drag

Supersedes PR #39 (`feat/riptide-strategy`), rebased onto main after futures M1–M4.

## Why

Riptide (private strategies submodule, pinned on main at `000532f`) imports
`src.core.fill_models`, which only exists on #39, so the registry skips it on main. #39 also
had three review findings:

- **Major**: `GapAwareFillModel` repriced a market-on-open (MOO) exit to the next bar's open
  but the fill still executed on the signal bar, so positions closed, and P&L was credited,
  one bar early, using a price from the future.
- **Minor**: `configs/riptide.yaml` said the orchestrator wires `GapAwareFillModel`; nothing
  did.
- **Minor**: the MOO integration test used the same open on the two bars after the signal,
  so a fill one bar late would also have passed.

## What changes

- `src/core/fill_models.py` (new, from #39):
  - `GapAwareFillModel`: a stop-market gapped through fills at the bar's open; an MOO-tagged
    market exit fills at the open of the bar it executes on.
  - `NextBarExecutionModule`: a Nautilus `SimulationModule` that, together with a 1 ns venue
    latency model, makes every command sent while bar T is handled reach the venue at the
    start of bar T+1, before its prices are matched. So an MOO exit now *executes* on T+1
    (fill and position-close timestamps are T+1's) at T+1's open.
  - `gap_aware_venue_options(fill_model)` / `bind_fill_model(fill_model, engine, bars)`.
- `src/core/fill_model.py`: `make_fill_model(seed, kind="default")`; `kind="gap_aware"`
  returns a `GapAwareFillModel` with the same seeded probabilities as the default.
- `src/core/backtest_venue.py`: a venue whose fill model is gap-aware also gets the latency
  model and module. Every other venue is set up exactly as before.
- `src/core/backtest_orchestrator.py`: builds the fill model from `request.fill_model` and
  binds it to the engine clock and bars (single and multi-instrument).
- `BacktestRequest.fill_model: "default" | "gap_aware"`, read from a YAML config's
  `backtest.fill_model`. `configs/riptide.yaml` asks for `gap_aware`.
- `IBKRCommissionModel.slippage_per_share` and `Settings.slippage_per_share`
  (`SLIPPAGE_PER_SHARE`, default 0), from #39. **Equities only**: a futures venue's
  `FuturesCommissionModel` never applies it (see design).
- Riptide config, unit tests and forked integration tests, from #39.

## Interfaces crossed

- **Request model / YAML schema**: `BacktestRequest.fill_model`; YAML `backtest.fill_model`
  (`default` | `gap_aware`; anything else is a validation error).
- **Run identity**: `compute_config_hash` includes `fill_model` only when it is not
  `default`, so every existing hash still names the same run.
- **Record shape**: `backtest_runs.config_snapshot.fill_model` is written only for a
  non-default model (`StrategyConfigSnapshot.fill_model`, omitted from the dump when unset),
  so ordinary rows stay byte-identical.
- **Settings**: `SLIPPAGE_PER_SHARE` env var.

No CLI flag, HTTP route, MCP tool contract, DB column or migration changes (`3f56b46c78eb`
stays the single head). Not a live-path change: no `src/core/live_*` or `live*` CLI module is
touched, so no guard list in `docs/agent/guard-lists.md` is affected. `FROZEN_VIEWS` files
(`src/api/**`, `templates/**`) are untouched.

## Size caps

`BacktestOrchestrator` baseline: 278 on #39's base, 277 on main; the slippage commit takes it
to 272 (`_make_fee_model` delegates to `IBKRCommissionModel.from_settings`) and the fill-model
wiring adds back what it needs (recorded in the same commit).

## Out of scope

- A CLI `--fill-model` flag and an MCP `fill_model` field: the registry/CLI path and research
  MCP runs keep the default fill model (Riptide's MOO exits then fill at the signal bar's
  close, as the strategy's docstring says). Reproducing a gap-aware run through the MCP is
  therefore not supported yet; its config hash differs, so it is flagged, not silently equal.
- Per-contract (tick) slippage for futures.
- The legacy `MinimalBacktestRunner` paths keep building the commission model without the
  slippage term.
- Riptide's universe ranking and portfolio cap (multi-instrument runner).
- The agent-config symlink commit from #39 (`3c26cad`): a separate PR.
