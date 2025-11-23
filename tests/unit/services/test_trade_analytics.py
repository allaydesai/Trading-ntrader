"""
Unit tests for trade analytics service.

Tests cover equity curve generation, drawdown calculations, and trade statistics.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.models.trade import (
    DrawdownMetrics,
    EquityCurvePoint,
    EquityCurveResponse,
    Trade,
    TradeStatistics,
)
from src.services.trade_analytics import (
    calculate_drawdowns,
    calculate_trade_statistics,
    generate_equity_curve,
)


class TestEquityCurveGeneration:
    """Test suite for equity curve generation logic."""

    def test_generate_equity_curve_with_empty_trades(self):
        """
        Test equity curve generation with no trades.

        Given: An empty trades list and initial capital of $100,000
        When: generate_equity_curve() is called
        Then: Returns single point at current time with initial capital
        """
        trades = []
        initial_capital = Decimal("100000.00")

        result = generate_equity_curve(trades, initial_capital)

        assert isinstance(result, EquityCurveResponse)
        assert len(result.points) == 1
        assert result.points[0].balance == initial_capital
        assert result.points[0].cumulative_return_pct == Decimal("0.00")
        assert result.points[0].trade_number == 0
        assert result.initial_capital == initial_capital
        assert result.final_balance == initial_capital
        assert result.total_return_pct == Decimal("0.00")

    def test_generate_equity_curve_with_mixed_wins_losses(self):
        """
        Test equity curve generation with winning and losing trades.

        Given: A sequence of 5 trades (3 wins, 2 losses) with varying P&L
        When: generate_equity_curve() is called with $100,000 initial capital
        Then: Equity curve shows cumulative balance after each trade exit
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = [
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("160.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                profit_loss=Decimal("995.00"),  # Win: +$995 (after $5 commission)
                profit_pct=Decimal("6.63"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=2,
                backtest_run_id=1,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("295.00"),
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=3),
                profit_loss=Decimal("-255.00"),  # Loss: -$255 (inc. $5 commission)
                profit_pct=Decimal("-1.70"),
                holding_period_seconds=3600,
                created_at=base_time + timedelta(hours=2),
            ),
            Trade(
                id=3,
                backtest_run_id=1,
                instrument_id="GOOGL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("145.00"),
                entry_timestamp=base_time + timedelta(hours=4),
                exit_timestamp=base_time + timedelta(hours=5),
                profit_loss=Decimal("370.00"),  # Win: +$370 (after $5 commission)
                profit_pct=Decimal("3.52"),
                holding_period_seconds=3600,
                created_at=base_time + timedelta(hours=4),
            ),
            Trade(
                id=4,
                backtest_run_id=1,
                instrument_id="TSLA",
                trade_id="trade-4",
                venue_order_id="order-4",
                order_side="BUY",
                quantity=Decimal("200"),
                entry_price=Decimal("180.00"),
                exit_price=Decimal("175.00"),
                entry_timestamp=base_time + timedelta(hours=6),
                exit_timestamp=base_time + timedelta(hours=7),
                profit_loss=Decimal("-1005.00"),  # Loss: -$1,005 (inc. $5 commission)
                profit_pct=Decimal("-2.79"),
                holding_period_seconds=3600,
                created_at=base_time + timedelta(hours=6),
            ),
            Trade(
                id=5,
                backtest_run_id=1,
                instrument_id="NVDA",
                trade_id="trade-5",
                venue_order_id="order-5",
                order_side="BUY",
                quantity=Decimal("60"),
                entry_price=Decimal("500.00"),
                exit_price=Decimal("520.00"),
                entry_timestamp=base_time + timedelta(hours=8),
                exit_timestamp=base_time + timedelta(hours=9),
                profit_loss=Decimal("1195.00"),  # Win: +$1,195 (after $5 commission)
                profit_pct=Decimal("3.98"),
                holding_period_seconds=3600,
                created_at=base_time + timedelta(hours=8),
            ),
        ]

        initial_capital = Decimal("100000.00")
        result = generate_equity_curve(trades, initial_capital)

        # Should have 6 points: initial + 5 trades
        assert len(result.points) == 6

        # Verify initial point
        assert result.points[0].balance == Decimal("100000.00")
        assert result.points[0].cumulative_return_pct == Decimal("0.00")
        assert result.points[0].trade_number == 0

        # Verify cumulative balances after each trade
        expected_balances = [
            Decimal("100000.00"),  # Initial
            Decimal("100995.00"),  # After trade 1: +995
            Decimal("100740.00"),  # After trade 2: -255
            Decimal("101110.00"),  # After trade 3: +370
            Decimal("100105.00"),  # After trade 4: -1005
            Decimal("101300.00"),  # After trade 5: +1195
        ]

        for i, point in enumerate(result.points):
            assert point.balance == expected_balances[i], (
                f"Point {i} balance mismatch: expected {expected_balances[i]}, got {point.balance}"
            )

        # Verify final metrics
        assert result.final_balance == Decimal("101300.00")
        expected_return = (
            (Decimal("101300.00") - Decimal("100000.00")) / Decimal("100000.00")
        ) * 100
        assert abs(result.total_return_pct - expected_return) < Decimal("0.01")

    def test_equity_curve_chronological_ordering(self):
        """
        Test that equity curve respects chronological order of trade exits.

        Given: Trades with exit timestamps in non-sequential order
        When: generate_equity_curve() is called
        Then: Equity curve points are ordered by exit_timestamp ascending
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        # Create trades with non-sequential IDs but sequential exit times
        trades = [
            Trade(
                id=3,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=5),  # Latest exit
                profit_loss=Decimal("495.00"),
                profit_pct=Decimal("3.30"),
                holding_period_seconds=18000,
                created_at=base_time,
            ),
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="MSFT",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("310.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),  # Earliest exit
                profit_loss=Decimal("495.00"),
                profit_pct=Decimal("3.30"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=2,
                backtest_run_id=1,
                instrument_id="GOOGL",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("143.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=3),  # Middle exit
                profit_loss=Decimal("220.00"),
                profit_pct=Decimal("2.10"),
                holding_period_seconds=10800,
                created_at=base_time,
            ),
        ]

        initial_capital = Decimal("100000.00")
        result = generate_equity_curve(trades, initial_capital)

        # Verify trades are processed in chronological order by exit_timestamp
        assert len(result.points) == 4  # Initial + 3 trades

        # Points should be ordered by exit timestamp
        assert result.points[1].timestamp == base_time + timedelta(hours=1)  # Trade 1
        assert result.points[2].timestamp == base_time + timedelta(hours=3)  # Trade 2
        assert result.points[3].timestamp == base_time + timedelta(hours=5)  # Trade 3

        # Verify cumulative balances reflect chronological order
        assert result.points[1].balance == Decimal("100495.00")  # After trade 1
        assert result.points[2].balance == Decimal("100715.00")  # After trade 2
        assert result.points[3].balance == Decimal("101210.00")  # After trade 3

    def test_equity_curve_with_open_trades_excluded(self):
        """
        Test that open trades (exit_timestamp=None) are excluded from equity curve.

        Given: A mix of closed trades and open trades
        When: generate_equity_curve() is called
        Then: Only closed trades appear in equity curve
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = [
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("160.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),  # Closed
                profit_loss=Decimal("995.00"),
                profit_pct=Decimal("6.63"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=2,
                backtest_run_id=1,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=None,  # Open trade (no exit)
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=None,
                profit_loss=None,
                profit_pct=None,
                holding_period_seconds=None,
                created_at=base_time + timedelta(hours=2),
            ),
            Trade(
                id=3,
                backtest_run_id=1,
                instrument_id="GOOGL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("145.00"),
                entry_timestamp=base_time + timedelta(hours=4),
                exit_timestamp=base_time + timedelta(hours=5),  # Closed
                profit_loss=Decimal("370.00"),
                profit_pct=Decimal("3.52"),
                holding_period_seconds=3600,
                created_at=base_time + timedelta(hours=4),
            ),
        ]

        initial_capital = Decimal("100000.00")
        result = generate_equity_curve(trades, initial_capital)

        # Should have 3 points: initial + 2 closed trades (trade 2 excluded)
        assert len(result.points) == 3
        assert result.points[0].balance == Decimal("100000.00")
        assert result.points[1].balance == Decimal("100995.00")  # After trade 1
        assert result.points[2].balance == Decimal("101365.00")  # After trade 3

    def test_equity_curve_with_zero_profit_trade(self):
        """
        Test equity curve handles breakeven trades (profit_loss = 0).

        Given: A trade with zero profit/loss (e.g., entry = exit, only commission)
        When: generate_equity_curve() is called
        Then: Balance remains unchanged (or decreases by commission only)
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = [
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("150.00"),  # Breakeven price
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                profit_loss=Decimal("-5.00"),  # Only commission cost
                profit_pct=Decimal("-0.03"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
                created_at=base_time,
            ),
        ]

        initial_capital = Decimal("100000.00")
        result = generate_equity_curve(trades, initial_capital)

        assert len(result.points) == 2
        assert result.points[0].balance == Decimal("100000.00")
        assert result.points[1].balance == Decimal("99995.00")  # Decreased by commission
        assert result.final_balance == Decimal("99995.00")


class TestTradeStatistics:
    """Test suite for trade statistics calculation."""

    def test_calculate_trade_statistics_with_no_trades(self):
        """
        Test trade statistics calculation with no trades.

        Given: An empty trades list
        When: calculate_trade_statistics() is called
        Then: Returns statistics with zero values and zero win rate
        """
        trades = []

        result = calculate_trade_statistics(trades)

        assert isinstance(result, TradeStatistics)
        assert result.total_trades == 0
        assert result.winning_trades == 0
        assert result.losing_trades == 0
        assert result.breakeven_trades == 0
        assert result.win_rate == Decimal("0.00")
        assert result.total_profit == Decimal("0.00")
        assert result.total_loss == Decimal("0.00")
        assert result.net_profit == Decimal("0.00")
        assert result.average_win == Decimal("0.00")
        assert result.average_loss == Decimal("0.00")
        assert result.largest_win == Decimal("0.00")
        assert result.largest_loss == Decimal("0.00")
        assert result.profit_factor is None
        assert result.expectancy == Decimal("0.00")
        assert result.max_consecutive_wins == 0
        assert result.max_consecutive_losses == 0

    def test_calculate_win_rate(self):
        """
        Test win rate calculation with mixed wins and losses.

        Given: 10 winning trades and 5 losing trades
        When: calculate_trade_statistics() is called
        Then: Win rate is 66.67%
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = []
        # Create 10 winning trades
        for i in range(10):
            trades.append(
                Trade(
                    id=i + 1,
                    backtest_run_id=1,
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
                    created_at=base_time,
                )
            )

        # Create 5 losing trades
        for i in range(10, 15):
            trades.append(
                Trade(
                    id=i + 1,
                    backtest_run_id=1,
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
                    created_at=base_time,
                )
            )

        result = calculate_trade_statistics(trades)

        assert result.total_trades == 15
        assert result.winning_trades == 10
        assert result.losing_trades == 5
        assert result.win_rate == Decimal("66.67")  # 10/15 * 100

    def test_calculate_profit_factor(self):
        """
        Test profit factor calculation.

        Given: Trades with total_profit=5000 and total_loss=2000
        When: calculate_trade_statistics() is called
        Then: Profit factor = 2.50 (5000 / 2000)
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = [
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("200.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                profit_loss=Decimal("5000.00"),  # Large win
                profit_pct=Decimal("33.33"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=2,
                backtest_run_id=1,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("260.00"),
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=3),
                profit_loss=Decimal("-2000.00"),  # Loss
                profit_pct=Decimal("-13.33"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
        ]

        result = calculate_trade_statistics(trades)

        assert result.total_profit == Decimal("5000.00")
        assert result.total_loss == Decimal("2000.00")
        assert result.net_profit == Decimal("3000.00")
        assert result.profit_factor == Decimal("2.50")  # 5000 / 2000

    def test_calculate_consecutive_streaks(self):
        """
        Test consecutive win/loss streak detection.

        Given: Sequence with 4 consecutive wins, then 2 consecutive losses
        When: calculate_trade_statistics() is called
        Then: max_consecutive_wins=4, max_consecutive_losses=2
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        # Sequence: W W W W L L W W L (4 wins, 2 losses max)
        profit_sequence = [500, 300, 400, 600, -200, -150, 250, 350, -100]

        trades = []
        for i, profit in enumerate(profit_sequence):
            trades.append(
                Trade(
                    id=i + 1,
                    backtest_run_id=1,
                    instrument_id="AAPL",
                    trade_id=f"trade-{i + 1}",
                    venue_order_id=f"order-{i + 1}",
                    order_side="BUY",
                    quantity=Decimal("100"),
                    entry_price=Decimal("150.00"),
                    exit_price=Decimal("155.00") if profit > 0 else Decimal("148.00"),
                    entry_timestamp=base_time + timedelta(hours=i),
                    exit_timestamp=base_time + timedelta(hours=i + 1),
                    profit_loss=Decimal(str(profit)),
                    profit_pct=Decimal("3.30") if profit > 0 else Decimal("-1.33"),
                    holding_period_seconds=3600,
                    created_at=base_time,
                )
            )

        result = calculate_trade_statistics(trades)

        assert result.max_consecutive_wins == 4
        assert result.max_consecutive_losses == 2

    def test_calculate_holding_period_statistics(self):
        """
        Test holding period calculations.

        Given: Trades with different holding periods
        When: calculate_trade_statistics() is called
        Then: Correctly calculates average, max, and min holding periods
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = [
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),  # 1 hour = 3600 sec
                profit_loss=Decimal("495.00"),
                profit_pct=Decimal("3.30"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=2,
                backtest_run_id=1,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("310.00"),
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=6),  # 4 hours = 14400 sec
                profit_loss=Decimal("495.00"),
                profit_pct=Decimal("3.30"),
                holding_period_seconds=14400,
                created_at=base_time,
            ),
            Trade(
                id=3,
                backtest_run_id=1,
                instrument_id="GOOGL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("143.00"),
                entry_timestamp=base_time + timedelta(hours=8),
                exit_timestamp=base_time + timedelta(hours=10),  # 2 hours = 7200 sec
                profit_loss=Decimal("220.00"),
                profit_pct=Decimal("2.10"),
                holding_period_seconds=7200,
                created_at=base_time,
            ),
        ]

        result = calculate_trade_statistics(trades)

        # Average: (3600 + 14400 + 7200) / 3 = 8400 seconds = 2.33 hours
        assert abs(result.avg_holding_period_hours - Decimal("2.33")) < Decimal("0.01")
        assert result.max_holding_period_hours == 4  # 14400 seconds
        assert result.min_holding_period_hours == 1  # 3600 seconds

    def test_calculate_statistics_with_breakeven_trades(self):
        """
        Test that breakeven trades (profit_loss <= 0 but near zero) are counted separately.

        Given: Trades with 2 wins, 1 loss, 1 breakeven (profit_loss = -5, only commission)
        When: calculate_trade_statistics() is called
        Then: breakeven_trades = 1, treated as a loss for win_rate
        """
        base_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        trades = [
            Trade(
                id=1,
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-1",
                venue_order_id="order-1",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("155.00"),
                entry_timestamp=base_time,
                exit_timestamp=base_time + timedelta(hours=1),
                profit_loss=Decimal("495.00"),  # Win
                profit_pct=Decimal("3.30"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=2,
                backtest_run_id=1,
                instrument_id="MSFT",
                trade_id="trade-2",
                venue_order_id="order-2",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("150.00"),  # Breakeven (only commission cost)
                entry_timestamp=base_time + timedelta(hours=2),
                exit_timestamp=base_time + timedelta(hours=3),
                profit_loss=Decimal("-5.00"),  # Breakeven after commission
                profit_pct=Decimal("-0.03"),
                holding_period_seconds=3600,
                commission_amount=Decimal("5.00"),
                created_at=base_time,
            ),
            Trade(
                id=3,
                backtest_run_id=1,
                instrument_id="GOOGL",
                trade_id="trade-3",
                venue_order_id="order-3",
                order_side="BUY",
                quantity=Decimal("75"),
                entry_price=Decimal("140.00"),
                exit_price=Decimal("145.00"),
                entry_timestamp=base_time + timedelta(hours=4),
                exit_timestamp=base_time + timedelta(hours=5),
                profit_loss=Decimal("370.00"),  # Win
                profit_pct=Decimal("3.52"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
            Trade(
                id=4,
                backtest_run_id=1,
                instrument_id="TSLA",
                trade_id="trade-4",
                venue_order_id="order-4",
                order_side="BUY",
                quantity=Decimal("50"),
                entry_price=Decimal("300.00"),
                exit_price=Decimal("295.00"),
                entry_timestamp=base_time + timedelta(hours=6),
                exit_timestamp=base_time + timedelta(hours=7),
                profit_loss=Decimal("-255.00"),  # Loss
                profit_pct=Decimal("-1.70"),
                holding_period_seconds=3600,
                created_at=base_time,
            ),
        ]

        result = calculate_trade_statistics(trades)

        assert result.total_trades == 4
        assert result.winning_trades == 2
        assert result.losing_trades == 1
        assert result.breakeven_trades == 1
        # Win rate: 2 / 4 = 50%
        assert result.win_rate == Decimal("50.00")


class TestDrawdownCalculation:
    """Test suite for drawdown calculation logic."""

    def test_calculate_drawdowns_with_no_drawdown(self):
        """
        Test drawdown calculation with monotonically increasing equity curve.

        Given: An equity curve that only goes up (no drawdowns)
        When: calculate_drawdowns() is called
        Then: Returns metrics with no drawdowns detected
        """
        # Create upward trending equity curve (no drawdowns)
        equity_curve = [
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),
                cumulative_return_pct=Decimal("0.00"),
                trade_number=0,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("101000.00"),
                cumulative_return_pct=Decimal("1.00"),
                trade_number=1,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("102500.00"),
                cumulative_return_pct=Decimal("2.50"),
                trade_number=2,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("104000.00"),
                cumulative_return_pct=Decimal("4.00"),
                trade_number=3,
            ),
        ]

        result = calculate_drawdowns(equity_curve)

        assert isinstance(result, DrawdownMetrics)
        assert result.max_drawdown is None
        assert len(result.top_drawdowns) == 0
        assert result.current_drawdown is None
        assert result.total_drawdown_periods == 0

    def test_calculate_drawdowns_single_period(self):
        """
        Test drawdown calculation with a single complete drawdown period.

        Given: An equity curve with one peak, trough, and recovery
        When: calculate_drawdowns() is called
        Then: Returns metrics with single recovered drawdown period
        """
        # Create equity curve with single drawdown:
        # $100k → $120k (peak) → $100k (trough) → $125k (recovery)
        equity_curve = [
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),
                cumulative_return_pct=Decimal("0.00"),
                trade_number=0,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("120000.00"),  # Peak
                cumulative_return_pct=Decimal("20.00"),
                trade_number=1,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("110000.00"),
                cumulative_return_pct=Decimal("10.00"),
                trade_number=2,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),  # Trough
                cumulative_return_pct=Decimal("0.00"),
                trade_number=3,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("125000.00"),  # Recovery (new peak)
                cumulative_return_pct=Decimal("25.00"),
                trade_number=4,
            ),
        ]

        result = calculate_drawdowns(equity_curve)

        assert result.total_drawdown_periods == 1
        assert result.max_drawdown is not None
        assert len(result.top_drawdowns) == 1
        assert result.current_drawdown is None

        # Verify drawdown details
        dd = result.max_drawdown
        assert dd.peak_balance == Decimal("120000.00")
        assert dd.trough_balance == Decimal("100000.00")
        assert dd.drawdown_amount == Decimal("20000.00")
        # Expected: (20000 / 120000) * 100 = 16.6667%
        assert abs(dd.drawdown_pct - Decimal("16.6667")) < Decimal("0.01")
        assert dd.recovered is True
        assert dd.recovery_timestamp == datetime(2025, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

    def test_calculate_drawdowns_multiple_periods(self):
        """
        Test drawdown calculation with multiple drawdown periods.

        Given: An equity curve with 3 distinct drawdown periods
        When: calculate_drawdowns() is called
        Then: Returns metrics with all 3 periods sorted by magnitude
        """
        # Create equity curve with 3 drawdown periods
        equity_curve = [
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),
                cumulative_return_pct=Decimal("0.00"),
                trade_number=0,
            ),
            # First drawdown: 5% ($100k → $105k → $100k → $110k)
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("105000.00"),  # Peak 1
                cumulative_return_pct=Decimal("5.00"),
                trade_number=1,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),  # Trough 1
                cumulative_return_pct=Decimal("0.00"),
                trade_number=2,
            ),
            # Second drawdown: 10% ($110k → $99k → $115k) - Largest
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("110000.00"),  # Peak 2
                cumulative_return_pct=Decimal("10.00"),
                trade_number=3,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("99000.00"),  # Trough 2
                cumulative_return_pct=Decimal("-1.00"),
                trade_number=4,
            ),
            # Third drawdown: 4.35% ($115k → $110k → $120k)
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 15, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("115000.00"),  # Peak 3
                cumulative_return_pct=Decimal("15.00"),
                trade_number=5,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 16, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("110000.00"),  # Trough 3
                cumulative_return_pct=Decimal("10.00"),
                trade_number=6,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 17, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("120000.00"),  # Recovery
                cumulative_return_pct=Decimal("20.00"),
                trade_number=7,
            ),
        ]

        result = calculate_drawdowns(equity_curve)

        assert result.total_drawdown_periods == 3
        assert result.max_drawdown is not None
        assert len(result.top_drawdowns) == 3
        assert result.current_drawdown is None

        # Verify largest drawdown is identified
        max_dd = result.max_drawdown
        assert max_dd.peak_balance == Decimal("110000.00")
        assert max_dd.trough_balance == Decimal("99000.00")
        assert max_dd.drawdown_amount == Decimal("11000.00")
        # Expected: (11000 / 110000) * 100 = 10%
        assert abs(max_dd.drawdown_pct - Decimal("10.00")) < Decimal("0.01")
        assert max_dd.recovered is True

        # Verify top drawdowns are sorted by percentage descending
        assert result.top_drawdowns[0].drawdown_pct >= result.top_drawdowns[1].drawdown_pct
        assert result.top_drawdowns[1].drawdown_pct >= result.top_drawdowns[2].drawdown_pct

    def test_calculate_drawdowns_ongoing_not_recovered(self):
        """
        Test drawdown calculation with ongoing drawdown (not yet recovered).

        Given: An equity curve ending in a drawdown that hasn't recovered
        When: calculate_drawdowns() is called
        Then: Returns metrics with current_drawdown populated and recovered=False
        """
        # Create equity curve with ongoing drawdown
        equity_curve = [
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),
                cumulative_return_pct=Decimal("0.00"),
                trade_number=0,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("120000.00"),  # Peak
                cumulative_return_pct=Decimal("20.00"),
                trade_number=1,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("110000.00"),
                cumulative_return_pct=Decimal("10.00"),
                trade_number=2,
            ),
            EquityCurvePoint(
                timestamp=datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
                balance=Decimal("100000.00"),  # Trough (ongoing)
                cumulative_return_pct=Decimal("0.00"),
                trade_number=3,
            ),
        ]

        result = calculate_drawdowns(equity_curve)

        # Should have 0 completed drawdowns
        assert result.total_drawdown_periods == 0
        assert len(result.top_drawdowns) == 0

        # Current drawdown should be present
        assert result.current_drawdown is not None
        assert result.max_drawdown is not None  # Should point to current drawdown

        # Verify current drawdown details
        dd = result.current_drawdown
        assert dd.peak_balance == Decimal("120000.00")
        assert dd.trough_balance == Decimal("100000.00")
        assert dd.drawdown_amount == Decimal("20000.00")
        # Expected: (20000 / 120000) * 100 = 16.6667%
        assert abs(dd.drawdown_pct - Decimal("16.6667")) < Decimal("0.01")
        assert dd.recovered is False
        assert dd.recovery_timestamp is None
