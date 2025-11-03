"""Unit tests for BacktestRepository data access layer."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

from src.db.repositories.backtest_repository import BacktestRepository
from src.db.base import Base


def get_worker_id(request):
    """Get pytest-xdist worker ID or 'master' if running without xdist."""
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    return worker_id


@pytest.fixture
async def async_test_engine(request):
    """Create async test database engine with schema isolation."""
    from src.config import get_settings

    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

    # Get worker ID for schema isolation
    worker_id = get_worker_id(request)
    schema_name = f"test_{worker_id}".replace("-", "_")

    engine = create_async_engine(async_url, echo=False)

    # Create schema and tables
    async with engine.begin() as conn:
        # Create schema
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))

        # Set search path to use our test schema
        await conn.execute(text(f"SET search_path TO {schema_name}"))

        # Create all tables in the test schema
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup: drop schema and all its contents
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))

    await engine.dispose()


@pytest.fixture
async def async_session(async_test_engine, request):
    """Create async test session."""
    # Get worker ID for schema isolation
    worker_id = get_worker_id(request)
    schema_name = f"test_{worker_id}".replace("-", "_")

    async_session_maker = async_sessionmaker(
        async_test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as session:
        # Set search path for this session
        await session.execute(text(f"SET search_path TO {schema_name}"))
        yield session


@pytest.fixture
def repository(async_session):
    """Create repository instance with test session."""
    return BacktestRepository(async_session)


class TestBacktestRepositoryCreate:
    """Test cases for BacktestRepository create operations."""

    @pytest.mark.asyncio
    async def test_create_backtest_run(self, repository, async_session):
        """Test creating a new backtest run record."""
        # Arrange
        run_id = uuid4()
        config = {
            "strategy": "sma_crossover",
            "params": {"fast": 10, "slow": 50},
        }

        # Act
        result = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name="SMA Crossover",
            strategy_type="sma_crossover",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("45.237"),
            config_snapshot=config,
        )

        await async_session.commit()

        # Assert
        assert result.run_id == run_id
        assert result.strategy_name == "SMA Crossover"
        assert result.instrument_symbol == "AAPL"
        assert result.initial_capital == Decimal("100000.00")
        assert result.execution_status == "success"
        assert result.config_snapshot == config

    @pytest.mark.asyncio
    async def test_create_performance_metrics(self, repository, async_session):
        """Test creating performance metrics for a backtest run."""
        # Arrange
        run_id = uuid4()
        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )
        await async_session.flush()

        # Act
        metrics = await repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            total_return=Decimal("0.2547"),
            final_balance=Decimal("125470.00"),
            sharpe_ratio=Decimal("1.85"),
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
        )

        await async_session.commit()

        # Assert
        assert metrics.backtest_run_id == backtest_run.id
        assert metrics.total_return == Decimal("0.2547")
        assert metrics.sharpe_ratio == Decimal("1.85")
        assert metrics.total_trades == 100


class TestBacktestRepositoryRetrieve:
    """Test cases for BacktestRepository retrieve operations."""

    @pytest.mark.asyncio
    async def test_find_by_run_id_success(self, repository, async_session):
        """Test finding a backtest by run_id when it exists."""
        # Arrange
        run_id = uuid4()
        await repository.create_backtest_run(
            run_id=run_id,
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )
        await async_session.commit()

        # Act
        result = await repository.find_by_run_id(run_id)

        # Assert
        assert result is not None
        assert result.run_id == run_id
        assert result.strategy_name == "Test Strategy"

    @pytest.mark.asyncio
    async def test_find_by_run_id_not_found(self, repository):
        """Test finding a backtest by run_id when it doesn't exist."""
        # Arrange
        non_existent_id = uuid4()

        # Act
        result = await repository.find_by_run_id(non_existent_id)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_find_by_run_ids(self, repository, async_session):
        """Test finding multiple backtests by their run_ids."""
        # Arrange
        run_ids = [uuid4(), uuid4(), uuid4()]

        for run_id in run_ids:
            await repository.create_backtest_run(
                run_id=run_id,
                strategy_name="Test Strategy",
                strategy_type="test",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="IBKR",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={},
            )

        await async_session.commit()

        # Act
        results = await repository.find_by_run_ids(run_ids)

        # Assert
        assert len(results) == 3
        result_run_ids = {r.run_id for r in results}
        assert result_run_ids == set(run_ids)

    @pytest.mark.asyncio
    async def test_find_by_strategy_filters_correctly(self, repository, async_session):
        """Test finding backtests filtered by strategy name."""
        # Arrange
        await repository.create_backtest_run(
            run_id=uuid4(),
            strategy_name="SMA Crossover",
            strategy_type="sma",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )

        await repository.create_backtest_run(
            run_id=uuid4(),
            strategy_name="RSI Strategy",
            strategy_type="rsi",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )

        await async_session.commit()

        # Act
        results = await repository.find_by_strategy("SMA Crossover", limit=10)

        # Assert
        assert len(results) == 1
        assert results[0].strategy_name == "SMA Crossover"

    @pytest.mark.asyncio
    async def test_find_recent_with_cursor_pagination(self, repository, async_session):
        """Test pagination using cursor (created_at timestamp)."""
        # Arrange
        run_ids = []
        for i in range(5):
            run_id = uuid4()
            run_ids.append(run_id)
            await repository.create_backtest_run(
                run_id=run_id,
                strategy_name=f"Strategy {i}",
                strategy_type="test",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="IBKR",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={},
            )

        await async_session.commit()

        # Act - Get first page
        page1 = await repository.find_recent(limit=2)
        assert len(page1) == 2

        # Get second page using last item from page1 as cursor
        cursor = page1[-1].created_at
        page2 = await repository.find_recent(limit=2, cursor=cursor)

        # Assert
        assert len(page2) == 2
        # Ensure no overlap between pages
        page1_ids = {r.run_id for r in page1}
        page2_ids = {r.run_id for r in page2}
        assert len(page1_ids & page2_ids) == 0

    @pytest.mark.asyncio
    async def test_find_top_performers_by_sharpe(self, repository, async_session):
        """Test finding top performing backtests by Sharpe ratio."""
        # Arrange
        performers = [
            (uuid4(), Decimal("2.5")),  # Best
            (uuid4(), Decimal("1.8")),  # Second
            (uuid4(), Decimal("0.9")),  # Third
        ]

        for run_id, sharpe in performers:
            backtest = await repository.create_backtest_run(
                run_id=run_id,
                strategy_name="Test",
                strategy_type="test",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="IBKR",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={},
            )

            await async_session.flush()

            await repository.create_performance_metrics(
                backtest_run_id=backtest.id,
                total_return=Decimal("0.15"),
                final_balance=Decimal("115000.00"),
                sharpe_ratio=sharpe,
                total_trades=10,
                winning_trades=6,
                losing_trades=4,
            )

        await async_session.commit()

        # Act
        top_performers = await repository.find_top_performers(
            metric="sharpe_ratio", limit=2
        )

        # Assert
        assert len(top_performers) == 2
        assert top_performers[0].metrics.sharpe_ratio == Decimal("2.5")
        assert top_performers[1].metrics.sharpe_ratio == Decimal("1.8")

    @pytest.mark.asyncio
    async def test_find_top_performers_excludes_null_sharpe(
        self, repository, async_session
    ):
        """Test that top performers query excludes runs with null metrics."""
        # Arrange
        # Create run without metrics
        await repository.create_backtest_run(
            run_id=uuid4(),
            strategy_name="No Metrics",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )

        # Create run with metrics
        backtest = await repository.create_backtest_run(
            run_id=uuid4(),
            strategy_name="With Metrics",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )

        await async_session.flush()

        await repository.create_performance_metrics(
            backtest_run_id=backtest.id,
            total_return=Decimal("0.15"),
            final_balance=Decimal("115000.00"),
            sharpe_ratio=Decimal("1.5"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

        await async_session.commit()

        # Act
        top_performers = await repository.find_top_performers(
            metric="sharpe_ratio", limit=10
        )

        # Assert
        assert len(top_performers) == 1
        assert top_performers[0].strategy_name == "With Metrics"

    @pytest.mark.asyncio
    async def test_find_top_performers_respects_limit(self, repository, async_session):
        """Test that top performers query respects limit parameter."""
        # Arrange - Create 5 runs with metrics
        for i in range(5):
            backtest = await repository.create_backtest_run(
                run_id=uuid4(),
                strategy_name=f"Strategy {i}",
                strategy_type="test",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="IBKR",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={},
            )

            await async_session.flush()

            await repository.create_performance_metrics(
                backtest_run_id=backtest.id,
                total_return=Decimal("0.15"),
                final_balance=Decimal("115000.00"),
                sharpe_ratio=Decimal(f"{i}.5"),
                total_trades=10,
                winning_trades=6,
                losing_trades=4,
            )

        await async_session.commit()

        # Act
        top_performers = await repository.find_top_performers(
            metric="sharpe_ratio", limit=3
        )

        # Assert
        assert len(top_performers) == 3

    @pytest.mark.asyncio
    async def test_count_by_strategy(self, repository, async_session):
        """Test counting backtests by strategy name."""
        # Arrange
        for _ in range(3):
            await repository.create_backtest_run(
                run_id=uuid4(),
                strategy_name="SMA Crossover",
                strategy_type="sma",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="IBKR",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={},
            )

        for _ in range(2):
            await repository.create_backtest_run(
                run_id=uuid4(),
                strategy_name="RSI Strategy",
                strategy_type="rsi",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="IBKR",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={},
            )

        await async_session.commit()

        # Act
        sma_count = await repository.count_by_strategy("SMA Crossover")
        rsi_count = await repository.count_by_strategy("RSI Strategy")

        # Assert
        assert sma_count == 3
        assert rsi_count == 2

    @pytest.mark.asyncio
    async def test_count_by_strategy_nonexistent(self, repository):
        """Test counting backtests for non-existent strategy returns 0."""
        # Act
        count = await repository.count_by_strategy("Nonexistent Strategy")

        # Assert
        assert count == 0
