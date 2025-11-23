"""
Integration tests for trades API endpoints.

Tests cover equity curve generation, trade statistics, and drawdown metrics endpoints.
Uses actual database queries to verify end-to-end functionality.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.web import app
from src.db.models.backtest import BacktestRun
from src.db.models.trade import Trade


class TestEquityCurveEndpoint:
    """Test suite for GET /api/equity-curve/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_equity_curve_endpoint_with_trades(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test equity curve endpoint returns correct data for backtest with trades.

        Given: A backtest with 5 completed trades
        When: GET /api/equity-curve/{id} is called
        Then: Returns 200 with equity curve points showing cumulative balances
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create sample trades
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = [
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("160.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                profit_loss=Decimal("995.00"),
                profit_pct=Decimal("6.63"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("295.00"),
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=3),
                profit_loss=Decimal("-255.00"),
                profit_pct=Decimal("-1.70"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="GOOGL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("145.00"),
                entry_timestamp=base_time + timedelta(hours=4),
                exit_timestamp=base_time + timedelta(hours=5),
                profit_loss=Decimal("370.00"),
                profit_pct=Decimal("3.52"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="TSLA",
                trade_id="trade-4",
                venue_order_id="order-4",
                order_side="BUY",
                quantity=Decimal("200"),
                entry_price=Decimal("180.00"),
                exit_price=Decimal("175.00"),
                entry_timestamp=base_time + timedelta(hours=6),
                exit_timestamp=base_time + timedelta(hours=7),
                profit_loss=Decimal("-1005.00"),
                profit_pct=Decimal("-2.79"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="NVDA",
                trade_id="trade-5",
                venue_order_id="order-5",
                order_side="BUY",
                quantity=Decimal("60"),
                entry_price=Decimal("500.00"),
                exit_price=Decimal("520.00"),
                entry_timestamp=base_time + timedelta(hours=8),
                exit_timestamp=base_time + timedelta(hours=9),
                profit_loss=Decimal("1195.00"),
                profit_pct=Decimal("3.98"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
            ),
        ]

        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        # Make API request using async client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/equity-curve/{sample_backtest_run.id}")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        assert "points" in data
        assert "initial_capital" in data
        assert "final_balance" in data
        assert "total_return_pct" in data

        # Should have 6 points: initial + 5 trades
        assert len(data["points"]) == 6

        # Verify initial point
        assert Decimal(data["points"][0]["balance"]) == sample_backtest_run.initial_capital
        assert Decimal(data["points"][0]["cumulative_return_pct"]) == Decimal("0.00")

        # Verify cumulative balances
        expected_balances = [
            "100000.00",  # Initial
            "100995.00",  # After trade 1
            "100740.00",  # After trade 2
            "101110.00",  # After trade 3
            "100105.00",  # After trade 4
            "101300.00",  # After trade 5
        ]

        for i, point in enumerate(data["points"]):
            assert Decimal(point["balance"]) == Decimal(expected_balances[i])

        # Verify final balance
        assert Decimal(data["final_balance"]) == Decimal("101300.00")

        # Cleanup: clear dependency overrides
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_equity_curve_endpoint_with_no_trades(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test equity curve endpoint with backtest that has no trades.

        Given: A backtest with zero trades
        When: GET /api/equity-curve/{id} is called
        Then: Returns 200 with single point at initial capital
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # No trades added to database

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/equity-curve/{sample_backtest_run.id}")

        assert response.status_code == 200
        data = response.json()

        # Should have only initial point
        assert len(data["points"]) == 1
        assert Decimal(data["points"][0]["balance"]) == sample_backtest_run.initial_capital
        assert Decimal(data["final_balance"]) == sample_backtest_run.initial_capital
        assert Decimal(data["total_return_pct"]) == Decimal("0.00")

        # Cleanup: clear dependency overrides
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_equity_curve_endpoint_with_nonexistent_backtest(self):
        """
        Test equity curve endpoint with invalid backtest ID.

        Given: A backtest ID that doesn't exist
        When: GET /api/equity-curve/{id} is called
        Then: Returns 404 Not Found
        """
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/equity-curve/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_equity_curve_chronological_ordering_via_api(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test that equity curve via API respects chronological order.

        Given: Trades inserted in non-chronological order
        When: GET /api/equity-curve/{id} is called
        Then: Returns points ordered by exit_timestamp ascending
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        # Insert trades in non-chronological order (by ID)
        trades = [
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=5),  # Latest
                profit_loss=Decimal("495.00"),
                profit_pct=Decimal("3.30"),
                holding_period_seconds=18000,
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="MSFT",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("310.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),  # Earliest
                profit_loss=Decimal("495.00"),
                profit_pct=Decimal("3.30"),
                holding_period_seconds=3600,
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="GOOGL",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("143.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=3),  # Middle
                profit_loss=Decimal("220.00"),
                profit_pct=Decimal("2.10"),
                holding_period_seconds=10800,
            ),
        ]

        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/equity-curve/{sample_backtest_run.id}")

        assert response.status_code == 200
        data = response.json()

        # Verify chronological ordering
        timestamps = [point["timestamp"] for point in data["points"][1:]]  # Skip initial
        assert timestamps[0] < timestamps[1] < timestamps[2]  # Ascending order

        # Verify cumulative balances reflect chronological order
        assert Decimal(data["points"][1]["balance"]) == Decimal("100495.00")  # Trade 1 first
        assert Decimal(data["points"][2]["balance"]) == Decimal("100715.00")  # Trade 2 second
        assert Decimal(data["points"][3]["balance"]) == Decimal("101210.00")  # Trade 3 third

        # Cleanup: clear dependency overrides
        app.dependency_overrides.clear()


class TestEquityCurvePerformance:
    """Test suite for equity curve performance requirements."""

    @pytest.mark.asyncio
    async def test_equity_curve_performance_with_1000_trades(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test equity curve generation performance with large dataset.

        Given: A backtest with 1000 trades
        When: GET /api/equity-curve/{id} is called
        Then: Returns 200 within 1 second (as per FR-006 requirement)
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        import time

        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        # Generate 1000 trades
        trades = []
        for i in range(1000):
            profit = Decimal("100.00") if i % 2 == 0 else Decimal("-50.00")
            trades.append(
                Trade(
                    backtest_run_id=sample_backtest_run.id,
                    instrument_id="AAPL",
                    trade_id=f"trade-{i}",
                    venue_order_id=f"order-{i}",
                    order_side="BUY",
                    quantity=Decimal("100"),
                    entry_price=Decimal("150.00"),
                    exit_price=Decimal("151.00") if i % 2 == 0 else Decimal("149.50"),
                    entry_timestamp=base_time + timedelta(minutes=i),
                    exit_timestamp=base_time + timedelta(minutes=i + 1),
                    profit_loss=profit,
                    profit_pct=Decimal("0.67") if i % 2 == 0 else Decimal("-0.33"),
                    holding_period_seconds=60,
                    commission_amount=Decimal("5.00"),
                )
            )

        # Bulk insert using async session
        db_session.add_all(trades)
        await db_session.commit()

        # Measure API response time
        start_time = time.time()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/equity-curve/{sample_backtest_run.id}")
        elapsed_time = time.time() - start_time

        # Verify success
        assert response.status_code == 200
        data = response.json()
        assert len(data["points"]) == 1001  # Initial + 1000 trades

        # Performance requirement: < 1 second (per FR-006)
        assert elapsed_time < 1.0, (
            f"Equity curve generation took {elapsed_time:.2f}s, exceeds 1 second requirement"
        )

        # Cleanup: clear dependency overrides
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_equity_curve_memory_efficiency_with_large_dataset(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test that equity curve calculation is memory-efficient.

        Given: A backtest with 1000 trades
        When: GET /api/equity-curve/{id} is called
        Then: Does not load all trade objects into memory at once
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        # Generate 1000 trades
        trades = []
        for i in range(1000):
            profit = Decimal("100.00") if i % 2 == 0 else Decimal("-50.00")
            trades.append(
                Trade(
                    backtest_run_id=sample_backtest_run.id,
                    instrument_id="AAPL",
                    trade_id=f"trade-{i}",
                    venue_order_id=f"order-{i}",
                    order_side="BUY",
                    quantity=Decimal("100"),
                    entry_price=Decimal("150.00"),
                    exit_price=Decimal("151.00") if i % 2 == 0 else Decimal("149.50"),
                    entry_timestamp=base_time + timedelta(minutes=i),
                    exit_timestamp=base_time + timedelta(minutes=i + 1),
                    profit_loss=profit,
                    profit_pct=Decimal("0.67") if i % 2 == 0 else Decimal("-0.33"),
                    holding_period_seconds=60,
                    commission_amount=Decimal("5.00"),
                )
            )

        db_session.add_all(trades)
        await db_session.commit()

        # Make API request
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/equity-curve/{sample_backtest_run.id}")

        assert response.status_code == 200
        data = response.json()

        # Verify all points are present
        assert len(data["points"]) == 1001

        # Verify final balance is correct (net profit calculation)
        # 500 wins * 100 = 50000
        # 500 losses * 50 = 25000
        # Net = 50000 - 25000 = 25000
        expected_final = Decimal("100000.00") + Decimal("25000.00")
        assert Decimal(data["final_balance"]) == expected_final

        # Cleanup: clear dependency overrides
        app.dependency_overrides.clear()


class TestTradeStatisticsEndpoint:
    """Test suite for GET /api/statistics/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_statistics_endpoint_with_mixed_trades(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test statistics endpoint returns correct metrics for mixed wins/losses.

        Given: A backtest with 10 wins and 5 losses
        When: GET /api/statistics/{id} is called
        Then: Returns 200 with correct win rate, profit factor, and streak metrics
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create trades: 10 wins, 5 losses
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = []

        # Create 10 winning trades
        for i in range(10):
            trades.append(
                Trade(
                    backtest_run_id=sample_backtest_run.id,
                    instrument_id="AAPL",
                    trade_id=f"trade-{i + 1}",
                    venue_order_id=f"order-{i + 1}",
                    order_side="BUY",
                    quantity=Decimal("100"),
                    entry_price=Decimal("150.00"),
                    exit_price=Decimal("155.00"),
                    entry_timestamp=base_time + timedelta(hours=i),
                    exit_timestamp=base_time + timedelta(hours=i + 1),
                    profit_loss=Decimal("495.00"),  # Win
                    profit_pct=Decimal("3.30"),
                    holding_period_seconds=3600,
                    commission_amount=Decimal("5.00"),
                )
            )

        # Create 5 losing trades
        for i in range(10, 15):
            trades.append(
                Trade(
                    backtest_run_id=sample_backtest_run.id,
                    instrument_id="MSFT",
                    trade_id=f"trade-{i + 1}",
                    venue_order_id=f"order-{i + 1}",
                    order_side="BUY",
                    quantity=Decimal("50"),
                    entry_price=Decimal("300.00"),
                    exit_price=Decimal("295.00"),
                    entry_timestamp=base_time + timedelta(hours=i),
                    exit_timestamp=base_time + timedelta(hours=i + 1),
                    profit_loss=Decimal("-255.00"),  # Loss
                    profit_pct=Decimal("-1.70"),
                    holding_period_seconds=3600,
                    commission_amount=Decimal("5.00"),
                )
            )

        db_session.add_all(trades)
        await db_session.commit()

        # Make API request
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/statistics/{sample_backtest_run.id}")

        assert response.status_code == 200
        data = response.json()

        # Verify trade counts
        assert data["total_trades"] == 15
        assert data["winning_trades"] == 10
        assert data["losing_trades"] == 5

        # Verify win rate (10/15 * 100 = 66.67%)
        assert Decimal(data["win_rate"]) == Decimal("66.67")

        # Verify consecutive streaks (10 consecutive wins, then 5 consecutive losses)
        assert data["max_consecutive_wins"] == 10
        assert data["max_consecutive_losses"] == 5

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_statistics_endpoint_with_no_trades(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test statistics endpoint handles backtest with no trades gracefully.

        Given: A backtest with zero trades
        When: GET /api/statistics/{id} is called
        Then: Returns 200 with all metrics set to zero/default values
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/statistics/{sample_backtest_run.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == "0.00"

        app.dependency_overrides.clear()


class TestDrawdownEndpoint:
    """Test suite for GET /api/drawdown/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_drawdown_endpoint_with_drawdown(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test drawdown endpoint returns correct metrics for backtest with drawdown.

        Given: A backtest with trades creating a peak-trough-recovery pattern
        When: GET /api/drawdown/{id} is called
        Then: Returns 200 with drawdown metrics showing max drawdown and recovery
        """
        # Override database dependency to use test session
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create trades that form a drawdown pattern:
        # $100k initial → $120k (peak) → $100k (trough) → $125k (recovery)
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = [
            # Trade 1: Win +$20k → Balance $120k (peak)
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("1000.00"),
                exit_price=Decimal("1200.00"),
                profit_loss=Decimal("20000.00"),
                profit_pct=Decimal("20.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                holding_period_seconds=3600,
            ),
            # Trade 2: Loss -$20k → Balance $100k (trough)
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("1200.00"),
                exit_price=Decimal("1000.00"),
                profit_loss=Decimal("-20000.00"),
                profit_pct=Decimal("-16.67"),
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=3),
                holding_period_seconds=3600,
            ),
            # Trade 3: Win +$25k → Balance $125k (recovery)
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="GOOGL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("1000.00"),
                exit_price=Decimal("1250.00"),
                profit_loss=Decimal("25000.00"),
                profit_pct=Decimal("25.00"),
                entry_timestamp=base_time + timedelta(hours=4),
                exit_timestamp=base_time + timedelta(hours=5),
                holding_period_seconds=3600,
            ),
        ]

        # Add trades to database
        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        # Call endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/drawdown/{sample_backtest_run.id}")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Verify max drawdown exists
        assert data["max_drawdown"] is not None
        max_dd = data["max_drawdown"]

        # Verify drawdown details
        # Peak: $120k, Trough: $100k, Drawdown: $20k, %: 16.67%
        assert max_dd["peak_balance"] == "120000.00"
        assert max_dd["trough_balance"] == "100000.00"
        assert max_dd["drawdown_amount"] == "20000.00"
        assert float(max_dd["drawdown_pct"]) == pytest.approx(16.6667, abs=0.01)
        assert max_dd["recovered"] is True
        assert max_dd["recovery_timestamp"] is not None

        # Verify drawdown period count
        assert data["total_drawdown_periods"] == 1

        # Verify top drawdowns
        assert len(data["top_drawdowns"]) == 1
        assert data["top_drawdowns"][0]["drawdown_pct"] == max_dd["drawdown_pct"]

        # No current drawdown (fully recovered)
        assert data["current_drawdown"] is None

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_drawdown_endpoint_with_no_drawdown(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test drawdown endpoint with monotonically increasing equity curve.

        Given: A backtest with only winning trades (no drawdowns)
        When: GET /api/drawdown/{id} is called
        Then: Returns 200 with empty drawdown metrics
        """
        # Override database dependency
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create only winning trades (no drawdown)
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = [
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("100.00"),
                exit_price=Decimal("110.00"),
                profit_loss=Decimal("1000.00"),
                profit_pct=Decimal("10.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                holding_period_seconds=3600,
            ),
            Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("200.00"),
                exit_price=Decimal("220.00"),
                profit_loss=Decimal("2000.00"),
                profit_pct=Decimal("10.00"),
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=3),
                holding_period_seconds=3600,
            ),
        ]

        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        # Call endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/drawdown/{sample_backtest_run.id}")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # No drawdowns
        assert data["max_drawdown"] is None
        assert len(data["top_drawdowns"]) == 0
        assert data["current_drawdown"] is None
        assert data["total_drawdown_periods"] == 0

        app.dependency_overrides.clear()


class TestTradesListEndpoint:
    """Test suite for GET /api/backtests/{id}/trades endpoint with pagination."""

    @pytest.mark.asyncio
    async def test_trades_list_with_pagination(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test trades list endpoint returns paginated results.

        Given: A backtest with 25 trades
        When: GET /api/backtests/{id}/trades?page=1&page_size=20 is called
        Then: Returns 200 with 20 trades and correct pagination metadata
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create 25 sample trades
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = []
        for i in range(25):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{i + 1}",
                venue_order_id=f"order-{i + 1}",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00") if i % 2 == 0 else Decimal("145.00"),
                entry_timestamp=base_time + timedelta(hours=i),
                exit_timestamp=base_time + timedelta(hours=i + 1),
                profit_loss=Decimal("500.00") if i % 2 == 0 else Decimal("-500.00"),
                profit_pct=Decimal("3.33") if i % 2 == 0 else Decimal("-3.33"),
                holding_period_seconds=3600,
                commission_amount=Decimal("0.00"),
            )
            trades.append(trade)

        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        # Call endpoint with pagination
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/trades",
                params={"page": 1, "page_size": 20},
            )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check trades array
        assert "trades" in data
        assert len(data["trades"]) == 20

        # Check pagination metadata
        assert "pagination" in data
        pagination = data["pagination"]
        assert pagination["total_items"] == 25
        assert pagination["total_pages"] == 2
        assert pagination["current_page"] == 1
        assert pagination["page_size"] == 20
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is False

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_trades_list_second_page(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test trades list endpoint returns second page correctly.

        Given: A backtest with 25 trades
        When: GET /api/backtests/{id}/trades?page=2&page_size=20 is called
        Then: Returns 200 with 5 trades (remaining items)
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create 25 sample trades
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(25):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{i + 1}",
                venue_order_id=f"order-{i + 1}",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=base_time + timedelta(hours=i),
                exit_timestamp=base_time + timedelta(hours=i + 1),
                profit_loss=Decimal("500.00"),
                profit_pct=Decimal("3.33"),
                holding_period_seconds=3600,
            )
            db_session.add(trade)
        await db_session.commit()

        # Call endpoint for page 2
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/trades",
                params={"page": 2, "page_size": 20},
            )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Should have 5 remaining trades
        assert len(data["trades"]) == 5
        assert data["pagination"]["current_page"] == 2
        assert data["pagination"]["has_next"] is False
        assert data["pagination"]["has_prev"] is True

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_trades_list_sorting_by_entry_timestamp(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test trades list endpoint supports sorting by entry_timestamp.

        Given: A backtest with 5 trades
        When: GET /api/backtests/{id}/trades?sort_by=entry_timestamp&sort_order=desc
        Then: Returns trades sorted by entry timestamp descending
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create trades with different timestamps
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        timestamps = [
            base_time + timedelta(hours=5),
            base_time + timedelta(hours=1),
            base_time + timedelta(hours=3),
        ]

        for idx, ts in enumerate(timestamps):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{idx}",
                venue_order_id=f"order-{idx}",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=ts,
                exit_timestamp=ts + timedelta(hours=1),
                profit_loss=Decimal("500.00"),
                profit_pct=Decimal("3.33"),
                holding_period_seconds=3600,
            )
            db_session.add(trade)
        await db_session.commit()

        # Call endpoint with descending sort
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/trades",
                params={"sort_by": "entry_timestamp", "sort_order": "desc"},
            )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check sorting metadata
        assert data["sorting"]["sort_by"] == "entry_timestamp"
        assert data["sorting"]["sort_order"] == "desc"

        # Verify trades are in descending order
        trades_list = data["trades"]
        assert len(trades_list) == 3
        # First should be latest (hours=5)
        # Last should be earliest (hours=1)
        assert trades_list[0]["trade_id"] == "trade-0"  # hours=5
        assert trades_list[1]["trade_id"] == "trade-2"  # hours=3
        assert trades_list[2]["trade_id"] == "trade-1"  # hours=1

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_trades_list_sorting_by_profit_loss(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test trades list endpoint supports sorting by profit_loss.

        Given: A backtest with trades having different profit/loss values
        When: GET /api/backtests/{id}/trades?sort_by=profit_loss&sort_order=desc
        Then: Returns trades sorted by profit descending (best trades first)
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create trades with different P&L
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        pnl_values = [
            Decimal("-500.00"),  # Loss
            Decimal("1000.00"),  # Big win
            Decimal("200.00"),  # Small win
        ]

        for idx, pnl in enumerate(pnl_values):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{idx}",
                venue_order_id=f"order-{idx}",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("150.00") + pnl / Decimal("100"),
                entry_timestamp=base_time + timedelta(hours=idx),
                exit_timestamp=base_time + timedelta(hours=idx + 1),
                profit_loss=pnl,
                profit_pct=Decimal("3.33"),
                holding_period_seconds=3600,
            )
            db_session.add(trade)
        await db_session.commit()

        # Call endpoint with profit_loss sorting descending
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/trades",
                params={"sort_by": "profit_loss", "sort_order": "desc"},
            )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Verify trades are in descending P&L order
        trades_list = data["trades"]
        assert len(trades_list) == 3
        assert trades_list[0]["trade_id"] == "trade-1"  # $1000 win
        assert trades_list[1]["trade_id"] == "trade-2"  # $200 win
        assert trades_list[2]["trade_id"] == "trade-0"  # -$500 loss

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_trades_list_with_different_page_sizes(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test trades list endpoint supports different page sizes (20/50/100).

        Given: A backtest with 100 trades
        When: Requesting with page_size=50
        Then: Returns 50 trades per page
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create 100 trades
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(100):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{i}",
                venue_order_id=f"order-{i}",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=base_time + timedelta(hours=i),
                exit_timestamp=base_time + timedelta(hours=i + 1),
                profit_loss=Decimal("500.00"),
                profit_pct=Decimal("3.33"),
                holding_period_seconds=3600,
            )
            db_session.add(trade)
        await db_session.commit()

        # Test page_size=50
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/trades",
                params={"page": 1, "page_size": 50},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["trades"]) == 50
        assert data["pagination"]["page_size"] == 50
        assert data["pagination"]["total_pages"] == 2

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_trades_list_with_zero_trades(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test trades list endpoint handles backtests with no trades gracefully.

        Given: A backtest with zero trades
        When: GET /api/backtests/{id}/trades is called
        Then: Returns 200 with empty trades array and zero pagination counts
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # No trades created, backtest exists but has no trades

        # Call endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/backtests/{sample_backtest_run.id}/trades")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        assert data["trades"] == []
        assert data["pagination"]["total_items"] == 0
        assert data["pagination"]["total_pages"] == 0
        assert data["pagination"]["current_page"] == 1
        assert data["pagination"]["has_next"] is False
        assert data["pagination"]["has_prev"] is False

        app.dependency_overrides.clear()


class TestTradeExportEndpoint:
    """Test suite for GET /api/backtests/{id}/export endpoint."""

    @pytest.mark.asyncio
    async def test_export_trades_as_csv(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test exporting trades to CSV format.

        Given: A backtest with 10 trades
        When: GET /api/backtests/{id}/export?format=csv is called
        Then: Returns 200 with CSV file containing all trades with correct headers
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create 10 sample trades
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = []
        for i in range(10):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id=f"AAPL{i}",
                trade_id=f"trade-{i + 1}",
                venue_order_id=f"order-{i + 1}",
                order_side="BUY",
                quantity=Decimal("100.50"),  # Fractional shares
                entry_price=Decimal("150.25"),
                exit_price=Decimal("155.75") if i % 2 == 0 else Decimal("145.50"),
                entry_timestamp=base_time + timedelta(hours=i),
                exit_timestamp=base_time + timedelta(hours=i + 1),
                profit_loss=Decimal("545.00") if i % 2 == 0 else Decimal("-477.38"),
                profit_pct=Decimal("3.61") if i % 2 == 0 else Decimal("-3.16"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
            )
            trades.append(trade)

        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        # Call export endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/export",
                params={"format": "csv"},
            )

        # Verify response
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "content-disposition" in response.headers
        assert "attachment" in response.headers["content-disposition"]
        assert (
            f"backtest_{sample_backtest_run.id}_trades.csv"
            in response.headers["content-disposition"]
        )

        # Parse CSV content
        csv_content = response.text
        lines = csv_content.strip().split("\n")

        # Verify header row
        expected_headers = [
            "instrument_id",
            "trade_id",
            "order_side",
            "entry_timestamp",
            "entry_price",
            "exit_timestamp",
            "exit_price",
            "quantity",
            "profit_loss",
            "profit_pct",
            "commission_amount",
            "holding_period_seconds",
        ]
        header_line = lines[0]
        assert all(header in header_line for header in expected_headers)

        # Verify we have 11 lines total (1 header + 10 data rows)
        assert len(lines) == 11

        # Verify first data row contains expected values
        first_data_row = lines[1]
        assert "AAPL0" in first_data_row
        assert "trade-1" in first_data_row
        assert "BUY" in first_data_row

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_export_trades_as_json(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test exporting trades to JSON format.

        Given: A backtest with 5 trades
        When: GET /api/backtests/{id}/export?format=json is called
        Then: Returns 200 with JSON array containing all trades
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create 5 sample trades
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trades = []
        for i in range(5):
            trade = Trade(
                backtest_run_id=sample_backtest_run.id,
                instrument_id=f"MSFT{i}",
                trade_id=f"trade-{i + 1}",
                venue_order_id=f"order-{i + 1}",
                order_side="SELL",
                quantity=Decimal("50.00"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("310.00"),
                entry_timestamp=base_time + timedelta(hours=i),
                exit_timestamp=base_time + timedelta(hours=i + 1),
                profit_loss=Decimal("500.00"),
                profit_pct=Decimal("3.33"),
                holding_period_seconds=3600,
                commission_amount=Decimal("0.00"),
            )
            trades.append(trade)

        for trade in trades:
            db_session.add(trade)
        await db_session.commit()

        # Call export endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/export",
                params={"format": "json"},
            )

        # Verify response
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert "content-disposition" in response.headers
        assert "attachment" in response.headers["content-disposition"]
        assert (
            f"backtest_{sample_backtest_run.id}_trades.json"
            in response.headers["content-disposition"]
        )

        # Parse JSON content
        json_data = response.json()

        # Verify structure
        assert isinstance(json_data, list)
        assert len(json_data) == 5

        # Verify first trade has all expected fields
        first_trade = json_data[0]
        assert "instrument_id" in first_trade
        assert "trade_id" in first_trade
        assert "order_side" in first_trade
        assert "entry_timestamp" in first_trade
        assert "entry_price" in first_trade
        assert "exit_timestamp" in first_trade
        assert "exit_price" in first_trade
        assert "quantity" in first_trade
        assert "profit_loss" in first_trade
        assert "profit_pct" in first_trade

        # Verify data values
        assert first_trade["instrument_id"] == "MSFT0"
        assert first_trade["trade_id"] == "trade-1"
        assert first_trade["order_side"] == "SELL"

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_export_csv_decimal_precision_preservation(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test that CSV export preserves decimal precision for quantities and prices.

        Given: A backtest with trades having fractional shares and prices
        When: Exporting to CSV
        Then: Decimal values are preserved with full precision
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create trade with precise decimal values
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trade = Trade(
            backtest_run_id=sample_backtest_run.id,
            instrument_id="GOOGL",
            trade_id="trade-precise",
            venue_order_id="order-precise",
            order_side="BUY",
            quantity=Decimal("123.456789"),  # High precision
            entry_price=Decimal("2500.12345"),
            exit_price=Decimal("2550.67890"),
            entry_timestamp=base_time,
            exit_timestamp=base_time + timedelta(hours=1),
            profit_loss=Decimal("6235.42"),
            profit_pct=Decimal("2.02"),
            holding_period_seconds=3600,
            commission_amount=Decimal("7.89"),
        )
        db_session.add(trade)
        await db_session.commit()

        # Call export endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/export",
                params={"format": "csv"},
            )

        assert response.status_code == 200

        # Parse CSV and verify precision
        csv_content = response.text
        lines = csv_content.strip().split("\n")
        data_row = lines[1]  # Skip header

        # Verify decimal precision is preserved
        assert "123.456789" in data_row or "123.46" in data_row  # Quantity
        assert "2500.12" in data_row  # Entry price
        assert "2550.68" in data_row or "2550.67" in data_row  # Exit price
        assert "6235.42" in data_row  # Profit/loss

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_export_csv_special_character_handling(
        self,
        db_session: AsyncSession,
        sample_backtest_run: BacktestRun,
    ):
        """
        Test that CSV export properly escapes special characters.

        Given: A backtest with trades containing symbols with special characters
        When: Exporting to CSV
        Then: Special characters are properly escaped and CSV is parseable
        """
        from src.api.dependencies import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # Create trade with special characters in symbol
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        trade = Trade(
            backtest_run_id=sample_backtest_run.id,
            instrument_id='SPY,"US",EQUITY',  # Contains comma and quotes
            trade_id="trade-special",
            venue_order_id="order-special",
            order_side="BUY",
            quantity=Decimal("100.00"),
            entry_price=Decimal("450.00"),
            exit_price=Decimal("460.00"),
            entry_timestamp=base_time,
            exit_timestamp=base_time + timedelta(hours=1),
            profit_loss=Decimal("1000.00"),
            profit_pct=Decimal("2.22"),
            holding_period_seconds=3600,
        )
        db_session.add(trade)
        await db_session.commit()

        # Call export endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/backtests/{sample_backtest_run.id}/export",
                params={"format": "csv"},
            )

        assert response.status_code == 200

        # Verify CSV is valid and parseable
        import csv
        import io

        csv_content = response.text
        csv_reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(csv_reader)

        # Should successfully parse despite special characters
        assert len(rows) == 1
        assert 'SPY,"US",EQUITY' in rows[0]["instrument_id"] or "SPY" in rows[0]["instrument_id"]

        app.dependency_overrides.clear()
