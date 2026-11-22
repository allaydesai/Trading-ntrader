# Tasks

## 1. Port from #39
- [x] 1.1 Cherry-pick the gap-aware fill model (`35159e4`) and slippage drag (`34503af`);
      resolve `test_size_caps.py` against main (orchestrator 277 -> 272).
- [x] 1.2 Port Riptide's config and tests (`137b0cc`) without its submodule repin.

## 2. Next-bar MOO exits (Major finding)
- [x] 2.1 Integration, forked (failing first): `tests/integration/test_riptide_run.py` —
      the green exit fills at T+1's open (distinct T+1/T+2 opens) and the fill and position
      close carry T+1's timestamp; the time stop likewise; an exit on the final bar stays open.
- [x] 2.2 Unit (failing first): `tests/unit/core/test_fill_models.py` — MOO reprices to the
      open of the bar being processed; venue options for gap-aware vs default models.
- [x] 2.3 Implement `NextBarExecutionModule`, `gap_aware_venue_options`, `bind_fill_model`.

## 3. Config wiring (Minor finding)
- [x] 3.1 Unit (failing first): `make_fill_model(kind=...)`, `BacktestRequest.fill_model`
      from YAML (and rejection of unknown names), hash and snapshot only when non-default.
- [x] 3.2 Integration, forked: the orchestrator runs `configs/riptide.yaml`-style requests on a
      gap-aware venue.
- [x] 3.3 Wire `backtest_venue.py` and the orchestrator; fix `configs/riptide.yaml`'s header.

## 4. Futures and slippage
- [x] 4.1 Component: a futures fill's fee is unchanged when the equity model carries
      slippage; an equity fill adds `qty x slippage`.
- [x] 4.2 Document the decision (design, `docs/agent/futures-backtesting.md`, Settings).

## 5. Verification
- [x] 5.1 Smoke before/after identical: sma_crossover and bollinger_reversal on
      firstrate-stocks AAPL 1-day; ES 1-hour H1 2024 `--contracts 1` for both.
- [x] 5.2 Riptide loads from the registry; ES `--contracts 1` (limit_offset_pct 0.2) and an
      equity run (`configs/riptide.yaml`, gap-aware) reported.
- [x] 5.3 Targeted tests serially; pre-commit hook without `--no-verify`; CI green.

## 6. Review (#56)
- [x] 6.1 Round 1: reject misaligned gap-aware runs, key bar opens by `ts_init`, move the
      Nautilus-dependent tests to the component tier, pin the default hashes, assert no open
      position after each exit path.
- [x] 6.2 Round 2: the alignment check covers every instrument in the engine, not per venue
      (Nautilus processes every venue after each data point).
