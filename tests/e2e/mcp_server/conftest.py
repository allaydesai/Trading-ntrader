"""E2E fixtures: the real server as a stdio subprocess, cleaning up the runs it persists."""

import os
import sys
from pathlib import Path
from uuid import UUID

import pytest
from mcp import Client, StdioServerParameters
from sqlalchemy import delete

from src.config import CatalogSettings
from src.db.models.backtest import BacktestRun
from src.db.models.research import ResearchStudy
from src.db.session_sync import get_sync_session

REPO_ROOT = Path(__file__).resolve().parents[3]
E2E_CATALOG = "e2e-test"


def e2e_data_present() -> bool:
    """Postgres reachable and the local e2e-test catalog on disk."""
    base = CatalogSettings().catalog_base_path
    if not base or not (REPO_ROOT / base / E2E_CATALOG).is_dir():
        return False
    try:
        with get_sync_session() as session:
            session.execute(delete(BacktestRun).where(BacktestRun.id < 0))
        return True
    except Exception:
        return False


requires_e2e_data = pytest.mark.skipif(
    not e2e_data_present(), reason="needs Postgres and the local e2e-test catalog"
)


GATES_MD = """# Promotion gates

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
G3:
  walk_forward_efficiency: 0.5
```
"""


@pytest.fixture
def vault(tmp_path) -> Path:
    path = tmp_path / "vault"
    (path / "Lab" / "results").mkdir(parents=True)
    (path / "System").mkdir()
    (path / "System" / "Gates.md").write_text(GATES_MD)
    return path


@pytest.fixture
def server_params(tmp_path, vault) -> StdioServerParameters:
    env = {
        **os.environ,
        "NTRADER_MCP_JOBS_DIR": str(tmp_path / "jobs"),
        "NTRADER_MCP_VAULT_PATH": str(vault),
        "NTRADER_MCP_DEFAULT_CATALOG": E2E_CATALOG,
    }
    return StdioServerParameters(
        command=sys.executable, args=["-m", "src.mcp_server"], cwd=str(REPO_ROOT), env=env
    )


@pytest.fixture
def created_runs():
    """Run ids a test persisted; deleted (with metrics and trades) afterwards."""
    run_ids: list[str] = []
    yield run_ids
    if run_ids:
        with get_sync_session() as session:
            for run in session.query(BacktestRun).filter(
                BacktestRun.run_id.in_([UUID(r) for r in run_ids])
            ):
                session.delete(run)


@pytest.fixture
def created_studies():
    """Study slugs a test opened; deleted (with ledger, candidates, events) afterwards."""
    slugs: list[str] = []
    yield slugs
    if slugs:
        with get_sync_session() as session:
            for study in session.query(ResearchStudy).filter(ResearchStudy.slug.in_(slugs)):
                session.delete(study)


async def call(client: Client, name: str, args: dict | None = None) -> dict:
    result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content
    return result.structured_content
