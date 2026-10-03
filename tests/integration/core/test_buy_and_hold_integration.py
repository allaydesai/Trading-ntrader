"""Buy-and-hold benchmark runs through the real engine (research MCP phase 1).

The benchmark must be scored by the same metric code as any strategy, so it has
to end the run flat: an open position at the end would leave its gain out of the
account balance. Requires --forked (Nautilus C/Rust extension isolation).
"""

from datetime import datetime, timezone
from decimal import Decimal
from math import floor
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.benchmarks import BENCHMARKS
from src.models.backtest_request import BacktestRequest
from src.services.firstrate.backtest_loader import load_from_catalog
from src.services.firstrate.catalog_manager import CatalogManager
from tests.integration.core.test_backtest_catalog_integration import (
    _make_instrument_row,
    synthetic_catalog,  # noqa: F401  # pytest fixture re-used from the catalog suite
)

pytestmark = pytest.mark.integration

START = datetime(2018, 1, 1, tzinfo=timezone.utc)
END = datetime(2018, 1, 11, tzinfo=timezone.utc)
BALANCE = Decimal("100000")


async def _run(catalog: Path, **params):
    from src.core.backtest_orchestrator import BacktestOrchestrator

    metadata = MagicMock()
    metadata.get_instrument_sync.return_value = _make_instrument_row("AAPL", "AAPL.NASDAQ")
    data = await load_from_catalog(
        catalog_name="e2e-test",
        ticker="AAPL",
        bar_type_spec="1-DAY-LAST",
        start=START,
        end=END,
        catalog_manager=CatalogManager(catalog),
        metadata_service=metadata,
    )
    benchmark = BENCHMARKS["buy_and_hold"]
    request = BacktestRequest(
        strategy_type="buy_and_hold",
        strategy_path=benchmark.strategy_path,
        config_path=benchmark.config_path,
        strategy_config=params,
        symbol="AAPL",
        instrument_id="AAPL.NAMED_CATALOG",
        start_date=START,
        end_date=END,
        bar_type="1-DAY-LAST",
        persist=False,
        starting_balance=BALANCE,
        catalog_name="e2e-test",
    )
    orchestrator = BacktestOrchestrator()
    try:
        result, _ = await orchestrator.execute(request, data.bars, data.instrument)
        assert orchestrator.engine is not None
        positions = orchestrator.engine.cache.positions()
        return result, positions, data.bars
    finally:
        orchestrator.dispose()


@pytest.mark.asyncio
async def test_buys_once_and_ends_flat(synthetic_catalog):  # noqa: F811
    result, positions, bars = await _run(synthetic_catalog)

    assert len(positions) == 1
    position = positions[0]
    assert position.is_closed, "benchmark must close at the end so the metrics count its gain"
    first_close = float(bars[0].close)
    assert float(position.peak_qty) == floor(float(BALANCE) / first_close)
    assert result.total_trades == 1


@pytest.mark.asyncio
async def test_result_tracks_the_price_move(synthetic_catalog):  # noqa: F811
    result, positions, _ = await _run(synthetic_catalog)

    position = positions[0]
    gross = float(position.peak_qty) * (position.avg_px_close - position.avg_px_open)
    assert gross > 0
    gain = result.final_balance - float(BALANCE)
    # Net of commission: within one percent of the gross price move.
    assert gain == pytest.approx(gross, rel=0.01)


@pytest.mark.asyncio
async def test_allocation_pct_scales_the_position(synthetic_catalog):  # noqa: F811
    _, positions, bars = await _run(synthetic_catalog, allocation_pct=Decimal("50"))

    first_close = float(bars[0].close)
    assert float(positions[0].peak_qty) == floor(float(BALANCE) * 0.5 / first_close)
