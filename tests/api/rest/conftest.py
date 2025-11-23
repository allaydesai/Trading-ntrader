"""
Pytest fixtures for REST API chart endpoint testing.

Provides test clients, mock services, and sample data for testing
chart API routes.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.dependencies import get_data_catalog_service, get_db
from src.api.web import app
from src.db.base import Base
from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.services.data_catalog import DataCatalogService


@pytest.fixture
def client() -> TestClient:
    """
    Get synchronous test client for REST API testing.

    Returns:
        TestClient configured with the FastAPI app

    Example:
        >>> def test_route(client):
        ...     response = client.get("/api/timeseries")
        ...     assert response.status_code == 200
    """
    return TestClient(app)


@pytest.fixture
async def async_test_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Create an in-memory SQLite database for testing.

    Sets up isolated database with all tables created for each test.

    Yields:
        AsyncSession for test database
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session_maker() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def mock_data_catalog_service() -> MagicMock:
    """
    Create a mock DataCatalogService for testing.

    Returns:
        MagicMock configured as DataCatalogService

    Example:
        >>> def test_timeseries(client, mock_data_catalog_service):
        ...     mock_data_catalog_service.query_bars.return_value = []
        ...     response = client.get("/api/timeseries?symbol=AAPL&...")
    """
    mock = MagicMock(spec=DataCatalogService)
    mock.query_bars = MagicMock(return_value=[])
    mock.get_availability = MagicMock(return_value=None)
    return mock


@pytest.fixture
def override_data_catalog(mock_data_catalog_service: MagicMock):
    """
    Override the get_data_catalog_service dependency for testing.

    Args:
        mock_data_catalog_service: Mock service to use

    Yields:
        Function that returns the mock service
    """

    def _override():
        return mock_data_catalog_service

    app.dependency_overrides[get_data_catalog_service] = _override
    yield _override
    app.dependency_overrides.pop(get_data_catalog_service, None)


@pytest.fixture
def override_get_db(async_test_db: AsyncSession):
    """
    Override the get_db dependency for testing.

    Args:
        async_test_db: Test database session

    Yields:
        Function that yields the test session
    """

    async def _override():
        yield async_test_db

    app.dependency_overrides[get_db] = _override
    yield _override
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def sample_run_id():
    """
    Generate a sample UUID for backtest run testing.

    Returns:
        UUID object for test run ID
    """
    return uuid4()


@pytest.fixture
def sample_backtest_run(sample_run_id) -> BacktestRun:
    """
    Create a sample BacktestRun for testing.

    Returns:
        BacktestRun instance with trades and equity data in config_snapshot
    """
    return BacktestRun(
        id=1,
        run_id=sample_run_id,
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("45.5"),
        config_snapshot={
            "fast_period": 10,
            "slow_period": 20,
            "trades": [
                {
                    "time": "2024-01-15",
                    "side": "buy",
                    "price": 185.50,
                    "quantity": 100,
                    "pnl": 0.0,
                },
                {
                    "time": "2024-01-20",
                    "side": "sell",
                    "price": 190.00,
                    "quantity": 100,
                    "pnl": 450.0,
                },
            ],
            "equity_curve": [
                {"time": "2024-01-01", "value": 100000.0},
                {"time": "2024-01-15", "value": 100000.0},
                {"time": "2024-01-20", "value": 100450.0},
                {"time": "2024-01-31", "value": 100450.0},
            ],
            "indicators": {
                "sma_fast": [
                    {"time": "2024-01-15", "value": 184.0},
                    {"time": "2024-01-20", "value": 186.5},
                ],
                "sma_slow": [
                    {"time": "2024-01-15", "value": 182.0},
                    {"time": "2024-01-20", "value": 183.0},
                ],
            },
        },
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_metrics() -> PerformanceMetrics:
    """
    Create sample PerformanceMetrics for testing.

    Returns:
        PerformanceMetrics instance with typical values
    """
    return PerformanceMetrics(
        id=1,
        backtest_run_id=1,
        total_return=Decimal("0.0045"),
        final_balance=Decimal("100450.00"),
        cagr=Decimal("0.055"),
        sharpe_ratio=Decimal("1.85"),
        sortino_ratio=Decimal("2.10"),
        max_drawdown=Decimal("-0.02"),
        max_drawdown_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
        calmar_ratio=Decimal("2.75"),
        volatility=Decimal("0.12"),
        total_trades=2,
        winning_trades=1,
        losing_trades=0,
        win_rate=Decimal("1.0"),
        profit_factor=Decimal("999.99"),
        expectancy=Decimal("450.00"),
        avg_win=Decimal("450.00"),
        avg_loss=Decimal("0.00"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_backtest_with_metrics(
    sample_backtest_run: BacktestRun, sample_metrics: PerformanceMetrics
) -> BacktestRun:
    """
    Create a sample BacktestRun with associated metrics.

    Args:
        sample_backtest_run: BacktestRun fixture
        sample_metrics: PerformanceMetrics fixture

    Returns:
        BacktestRun with metrics relationship populated
    """
    sample_backtest_run.metrics = sample_metrics
    return sample_backtest_run
