"""The orchestrator stamps run provenance on every persisted run (MCP spec S2.3)."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.backtest_orchestrator import BacktestOrchestrator
from src.models.backtest_request import BacktestRequest
from src.models.run_provenance import RunProvenance

pytestmark = pytest.mark.component

MODULE = "src.core.backtest_orchestrator"
PROVENANCE = RunProvenance(
    git_commit="a" * 40, git_dirty=False, strategies_commit="b" * 40, config_hash="c" * 64
)


def _request() -> BacktestRequest:
    return BacktestRequest(
        strategy_type="sma_crossover",
        strategy_path="src.core.strategies.sma_crossover:SMACrossover",
        config_path="src.core.strategies.sma_crossover:SMAConfig",
        strategy_config={"fast_period": 10, "slow_period": 20},
        symbol="QQQ",
        instrument_id="QQQ.NAMED_CATALOG",
        start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2020, 12, 31, tzinfo=timezone.utc),
        bar_type="1-DAY-LAST",
        catalog_name="e2e-test",
    )


@pytest.fixture
def service():
    svc = MagicMock()
    svc.save_backtest_results = AsyncMock(return_value=MagicMock(id=1))
    svc.save_failed_backtest = AsyncMock(return_value=MagicMock(id=1))
    session = MagicMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def fake_session():
        yield session

    with (
        patch(f"{MODULE}.get_session", fake_session),
        patch(f"{MODULE}.BacktestPersistenceService", return_value=svc),
    ):
        yield svc


async def test_failed_run_records_provenance(service):
    await BacktestOrchestrator()._persist_failed(
        request=_request(),
        error_message="boom",
        execution_duration=Decimal("1"),
        provenance=PROVENANCE,
    )
    assert service.save_failed_backtest.call_args.kwargs["provenance"] == PROVENANCE


async def test_successful_run_records_provenance(service):
    orchestrator = BacktestOrchestrator()
    with patch.object(orchestrator, "_extract_equity_curve", return_value=None):
        await orchestrator._persist_results(
            run_id=uuid4(),
            request=_request(),
            result=MagicMock(),
            execution_duration=Decimal("1"),
            provenance=PROVENANCE,
        )
    assert service.save_backtest_results.call_args.kwargs["provenance"] == PROVENANCE


async def test_execute_computes_provenance_when_none_given(service):
    orchestrator = BacktestOrchestrator()
    with (
        patch(f"{MODULE}.run_provenance", return_value=PROVENANCE) as computed,
        patch.object(orchestrator, "_setup_engine", side_effect=RuntimeError("engine")),
        pytest.raises(RuntimeError),
    ):
        await orchestrator.execute(_request(), bars=[MagicMock()], instrument=MagicMock())
    computed.assert_called_once()
    assert service.save_failed_backtest.call_args.kwargs["provenance"] == PROVENANCE


async def test_execute_uses_given_provenance(service):
    orchestrator = BacktestOrchestrator()
    with (
        patch(f"{MODULE}.run_provenance") as computed,
        patch.object(orchestrator, "_setup_engine", side_effect=RuntimeError("engine")),
        pytest.raises(RuntimeError),
    ):
        await orchestrator.execute(
            _request(), bars=[MagicMock()], instrument=MagicMock(), provenance=PROVENANCE
        )
    computed.assert_not_called()
    assert service.save_failed_backtest.call_args.kwargs["provenance"] == PROVENANCE
