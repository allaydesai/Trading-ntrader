"""Shared set-up for study integration tests: settings, a vault with gates, a job runner."""

from datetime import date
from pathlib import Path
from typing import Any

from src.mcp_server import validation
from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.settings import McpSettings
from src.mcp_server.studies import lifecycle
from src.mcp_server.studies.spec import build_study_spec

GATES_BLOCK = """# Promotion gates

```yaml ntrader-gates
G0:
  min_trades: 30
  clean_tree: true
  benchmark_present: true
G1:
  is_profit_factor: 1.3
  is_expectancy_gt: 0
  beats_benchmark_on_one: [sharpe_ratio, calmar_ratio, max_drawdown]
  positive_sub_periods: {count: 3, of: 4}
G2:
  oos_sharpe_vs_is: 0.5
  oos_profit_factor: 1.1
  neighbourhood_sharpe: 0.7
  deflated_sharpe_prob: 0.95
  oos_drawdown_within_mc: true
  trials_warn_above: 50
G3:
  walk_forward_efficiency: 0.5
```
"""

STUDY = dict(
    slug="sma-aapl",
    title="SMA crossover on AAPL",
    hypothesis="Trends persist on large caps",
    strategy="sma_crossover",
    symbols=["AAPL", "msft"],
    in_sample_start=date(2018, 1, 1),
    in_sample_end=date(2019, 12, 31),
    out_of_sample_start=date(2020, 1, 1),
    out_of_sample_end=date(2020, 12, 31),
    param_space={"fast_period": {"values": [5, 10]}, "slow_period": {"min": 20, "max": 60}},
    trial_budget=3,
    tags=["trend"],
)


def coverage(symbol: str, catalog: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "catalog": catalog,
        "nautilus_id": f"{symbol.upper()}.NASDAQ",
        "backtestable": True,
        "timeframes": {
            "1-DAY": {
                "start": "2000-01-03T00:00:00+00:00",
                "start_is_exact": True,
                "end": "2026-05-01T00:00:00+00:00",
                "bars": 6000,
            }
        },
    }


def make_ctx(tmp_path: Path, monkeypatch, *, gates: str | None = GATES_BLOCK) -> ServerContext:
    """Settings over a temp vault and jobs dir; catalog coverage stubbed for any symbol."""
    monkeypatch.setattr(validation, "catalog_availability", coverage)
    vault = tmp_path / "vault"
    (vault / "System").mkdir(parents=True)
    (vault / "Lab" / "results").mkdir(parents=True)
    if gates is not None:
        (vault / "System" / "Gates.md").write_text(gates)
    settings = McpSettings(
        _env_file=None, jobs_dir=tmp_path / "jobs", vault_path=vault, default_catalog="e2e-test"
    )
    runner = JobRunner(JobStore(settings.jobs_dir), timeout_s=30)
    return ServerContext(settings=settings, runner=runner)


def open_study(ctx: ServerContext, **overrides: Any) -> dict[str, Any]:
    return lifecycle.create_study(ctx.settings, build_study_spec(**{**STUDY, **overrides}))


class FakeRun:
    """A stored run as ``freeze_candidate`` reads it, without running a backtest."""

    def __init__(self, run_id: str, *, symbol: str, params: dict[str, Any], fill_seed: int):
        from uuid import UUID

        self.run_id = UUID(run_id)
        self.strategy_type = "sma_crossover"
        self.instrument_symbol = symbol
        self.config_snapshot = {"config": params, "fill_seed": fill_seed}
        self.data_source = "catalog:e2e-test"
        self.initial_capital = 1_000_000
        self.git_commit = None


def freeze_fake(
    ctx: ServerContext,
    monkeypatch,
    *,
    params: dict[str, Any],
    symbol: str = "AAPL",
    fill_seed: int = 42,
    study: str = "sma-aapl",
) -> str:
    """Freeze a candidate from an in-sample trial whose run is a ``FakeRun``."""
    from uuid import uuid4

    from src.mcp_server.studies import candidates
    from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial

    job, _ = prepare_in_sample(
        study,
        params={k: v for k, v in params.items() if k in ("fast_period", "slow_period")},
        strategy=None,
        symbol=None,
        start=None,
        end=None,
        timeframe=None,
        catalog=None,
        starting_balance=None,
        over_budget_reason="fixture",
    )
    recorded = reserve_trial(ctx.runner, job)
    run_id = str(uuid4())
    ctx.runner.store.update(recorded["job_id"], state="succeeded", run_id=run_id)
    run = FakeRun(run_id, symbol=symbol, params=params, fill_seed=fill_seed)
    monkeypatch.setattr(candidates, "load_run", lambda session, rid: run)
    candidates.freeze_candidate(ctx.runner.store, study, run_id, None)
    return run_id
