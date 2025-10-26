"""Unit tests for BacktestRepository data access layer."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.db.repositories.backtest_repository import BacktestRepository
from src.db.base import Base


@pytest.fixture
async def async_test_engine():
    """Create async test database engine."""
    from src.config import get_settings

    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

    engine = create_async_engine(async_url, echo=False)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def async_session(async_test_engine):
    """Create async test session."""
    async_session_maker = async_sessionmaker(
        async_test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as session:
        yield session


@pytest.fixture
def repository(async_session):
    """Create repository instance with test session."""
    return BacktestRepository(async_session)


class TestBacktestRepositoryCreate:
    """Test suite for BacktestRepository creation methods."""

    @pytest.mark.asyncio
    async def test_create_backtest_run(self, repository, async_session):
        """Test creating a backtest run record."""
        run_id = uuid4()
        start_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2023, 12, 31, tzinfo=timezone.utc)

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=start_date,
            end_date=end_date,
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("45.237"),
            config_snapshot={
                "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
                "config_path": "config/strategies/sma_crossover.yaml",
                "version": "1.0",
                "config": {"fast_period": 10, "slow_period": 50},
            },
        )

        assert backtest_run.id is not None
        assert backtest_run.run_id == run_id
        assert backtest_run.strategy_name == "SMA Crossover"
        assert backtest_run.created_at is not None

    @pytest.mark.asyncio
    async def test_create_performance_metrics(self, repository, async_session):
        """Test creating performance metrics record."""
        # First create a backtest run
        run_id = uuid4()
        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={
                "strategy_path": "test",
                "config_path": "test",
                "version": "1.0",
                "config": {},
            },
        )

        # Create metrics
        metrics = await repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            total_return=Decimal("0.25"),
            final_balance=Decimal("125000.00"),
            cagr=Decimal("0.22"),
            sharpe_ratio=Decimal("1.85"),
            sortino_ratio=Decimal("2.10"),
            max_drawdown=Decimal("-0.15"),
            max_drawdown_date=datetime(2023, 6, 15, tzinfo=timezone.utc),
            calmar_ratio=Decimal("1.50"),
            volatility=Decimal("0.12"),
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("0.60"),
            profit_factor=Decimal("1.75"),
            expectancy=Decimal("250.00"),
            avg_win=Decimal("500.00"),
            avg_loss=Decimal("-285.71"),
        )

        assert metrics.id is not None
        assert metrics.backtest_run_id == backtest_run.id
        assert metrics.total_return == Decimal("0.25")
        assert metrics.sharpe_ratio == Decimal("1.85")


class TestBacktestRepositoryRetrieve:
    """Test suite for BacktestRepository retrieval methods."""

    @pytest.mark.asyncio
    async def test_find_by_run_id_success(self, repository, async_session):
        """Test finding backtest by run_id."""
        run_id = uuid4()

        # Create a backtest
        await repository.create_backtest_run(
            run_id=run_id,
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={
                "strategy_path": "test",
                "config_path": "test",
                "version": "1.0",
                "config": {},
            },
        )

        await async_session.commit()

        # Find it
        found = await repository.find_by_run_id(run_id)

        assert found is not None
        assert found.run_id == run_id

    @pytest.mark.asyncio
    async def test_find_by_run_id_not_found(self, repository):
        """Test finding non-existent backtest returns None."""
        non_existent_id = uuid4()
        found = await repository.find_by_run_id(non_existent_id)

        assert found is None

    @pytest.mark.asyncio
    async def test_find_recent(self, repository, async_session):
        """Test finding recent backtests."""
        # Create 5 backtest runs
        for i in range(5):
            await repository.create_backtest_run(
                run_id=uuid4(),
                strategy_name=f"Strategy {i}",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={
                    "strategy_path": "test",
                    "config_path": "test",
                    "version": "1.0",
                    "config": {},
                },
            )

        await async_session.commit()

        # Find recent (limit 3)
        recent = await repository.find_recent(limit=3)

        assert len(recent) == 3
        # Should be ordered by created_at DESC
        for i in range(len(recent) - 1):
            assert recent[i].created_at >= recent[i + 1].created_at

    @pytest.mark.asyncio
    async def test_find_recent_with_cursor_pagination(self, repository, async_session):
        """
        Test cursor-based pagination for find_recent().

        Tests that cursor pagination correctly fetches the next page of results
        without duplicates or gaps.
        """
        # Create 50 backtest runs with slight time delays to ensure ordering
        import asyncio

        created_runs = []

        for i in range(50):
            run = await repository.create_backtest_run(
                run_id=uuid4(),
                strategy_name=f"Strategy {i}",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={
                    "strategy_path": "test",
                    "config_path": "test",
                    "version": "1.0",
                    "config": {},
                },
            )
            created_runs.append(run)
            # Small delay to ensure different created_at timestamps
            await asyncio.sleep(0.001)

        await async_session.commit()

        # Get first page (20 results)
        page1 = await repository.find_recent(limit=20)
        assert len(page1) == 20

        # Get second page using cursor from last item of page1
        cursor = (page1[-1].created_at, page1[-1].id)
        page2 = await repository.find_recent(limit=20, cursor=cursor)

        assert len(page2) == 20

        # Verify no duplicates between pages
        page1_ids = {run.id for run in page1}
        page2_ids = {run.id for run in page2}
        assert page1_ids.isdisjoint(page2_ids)

        # Verify ordering is maintained
        assert page1[0].created_at >= page1[-1].created_at
        assert page2[0].created_at >= page2[-1].created_at
        assert page1[-1].created_at >= page2[0].created_at

    @pytest.mark.asyncio
    async def test_find_by_strategy_filters_correctly(self, repository, async_session):
        """
        Test that find_by_strategy() returns only matching strategy backtests.

        Given: Backtests for multiple strategies
        When: Querying by specific strategy name
        Then: Only backtests for that strategy are returned
        """
        # Create backtests for different strategies
        strategies = ["SMA Crossover", "RSI Mean Reversion", "Momentum"]

        for strategy in strategies:
            for i in range(5):
                await repository.create_backtest_run(
                    run_id=uuid4(),
                    strategy_name=strategy,
                    strategy_type="test",
                    instrument_symbol="TEST",
                    start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                    end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                    initial_capital=Decimal("100000.00"),
                    data_source="Mock",
                    execution_status="success",
                    execution_duration_seconds=Decimal("10.0"),
                    config_snapshot={
                        "strategy_path": "test",
                        "config_path": "test",
                        "version": "1.0",
                        "config": {},
                    },
                )

        await async_session.commit()

        # Query for "SMA Crossover" only
        sma_backtests = await repository.find_by_strategy("SMA Crossover")

        assert len(sma_backtests) == 5
        assert all(bt.strategy_name == "SMA Crossover" for bt in sma_backtests)

        # Query for "RSI Mean Reversion"
        rsi_backtests = await repository.find_by_strategy("RSI Mean Reversion")

        assert len(rsi_backtests) == 5
        assert all(bt.strategy_name == "RSI Mean Reversion" for bt in rsi_backtests)

    @pytest.mark.asyncio
    async def test_find_by_run_ids(self, repository, async_session):
        """
        Test finding multiple backtests by their run IDs.

        Given: Database with multiple backtest runs
        When: Querying with a list of specific run IDs
        Then: Returns only the backtests matching those IDs
        """
        # Create 5 backtest runs and track their run_ids
        run_ids = []

        for i in range(5):
            run_id = uuid4()
            run_ids.append(run_id)

            backtest_run = await repository.create_backtest_run(
                run_id=run_id,
                strategy_name=f"Strategy {i}",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_status="success",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot={
                    "strategy_path": "test",
                    "config_path": "test",
                    "version": "1.0",
                    "config": {},
                },
            )

            # Add metrics
            await repository.create_performance_metrics(
                backtest_run_id=backtest_run.id,
                total_return=Decimal(str(0.1 + i * 0.05)),
                final_balance=Decimal(str(110000 + i * 5000)),
                sharpe_ratio=Decimal(str(1.0 + i * 0.2)),
                total_trades=10,
                winning_trades=6,
                losing_trades=4,
            )

        await async_session.commit()

        # Query for first 3 run IDs
        selected_ids = run_ids[:3]
        found_backtests = await repository.find_by_run_ids(selected_ids)

        # Assertions
        assert len(found_backtests) == 3
        found_run_ids = {bt.run_id for bt in found_backtests}
        assert found_run_ids == set(selected_ids)

        # Verify metrics are loaded
        assert all(bt.metrics is not None for bt in found_backtests)

        # Verify results are ordered by created_at DESC
        for i in range(len(found_backtests) - 1):
            assert found_backtests[i].created_at >= found_backtests[i + 1].created_at
