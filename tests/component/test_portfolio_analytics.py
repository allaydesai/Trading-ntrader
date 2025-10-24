"""Test suite for PortfolioAnalytics module."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock
import numpy as np

from src.core.analytics import PortfolioAnalytics


@pytest.mark.component
class TestPortfolioAnalytics:
    """Test PortfolioAnalytics functionality."""

    @pytest.fixture
    def analytics(self):
        """Create PortfolioAnalytics instance for testing."""
        return PortfolioAnalytics()

    def test_calculate_avg_duration(self, analytics):
        """Test average trade duration calculation."""
        # Create positions with known durations
        positions = []
        base_time = datetime(2024, 1, 15, 10, 0)

        for i in range(3):
            position = Mock()
            position.opened_time = base_time + timedelta(hours=i)
            position.closed_time = base_time + timedelta(
                hours=i + 2
            )  # 2 hour duration each
            positions.append(position)

        duration = analytics.calculate_avg_duration(positions)
        assert duration == 2.0  # 2 hours average

    def test_calculate_avg_duration_empty_list(self, analytics):
        """Test average duration with empty position list."""
        duration = analytics.calculate_avg_duration([])
        assert duration is None

    def test_calculate_avg_duration_no_closed_time(self, analytics):
        """Test average duration with positions missing closed_time."""
        position = Mock()
        position.opened_time = datetime(2024, 1, 15, 10, 0)
        position.closed_time = None

        duration = analytics.calculate_avg_duration([position])
        assert duration is None

    def test_calculate_pnl_statistics(self, analytics):
        """Test PnL statistics calculation."""
        # Create positions with known PnLs
        positions = []
        pnls = [100.0, -50.0, 200.0, -25.0, 150.0]  # 3 winners, 2 losers

        for i, pnl in enumerate(pnls):
            position = Mock()
            position.realized_pnl = pnl
            positions.append(position)

        stats = analytics.calculate_pnl_statistics(positions)

        assert stats["total_pnl"] == sum(pnls)  # 375.0
        assert stats["avg_pnl"] == np.mean(pnls)  # 75.0
        assert stats["win_rate"] == 0.6  # 3/5 = 60%
        assert stats["max_win"] == 200.0
        assert stats["max_loss"] == -50.0
        assert "std_pnl" in stats

    def test_calculate_pnl_statistics_empty_list(self, analytics):
        """Test PnL statistics with empty position list."""
        stats = analytics.calculate_pnl_statistics([])
        assert stats["total_pnl"] == 0.0
        assert stats["avg_pnl"] == 0.0
        assert stats["win_rate"] == 0.0

    def test_calculate_pnl_statistics_no_realized_pnl(self, analytics):
        """Test PnL statistics with positions missing realized_pnl."""
        position = Mock()
        position.realized_pnl = None

        stats = analytics.calculate_pnl_statistics([position])
        assert stats["total_pnl"] == 0.0
        assert stats["avg_pnl"] == 0.0

    def test_calculate_side_statistics(self, analytics):
        """Test position side statistics calculation."""
        # Create positions with mixed sides
        positions = []
        long_pnls = [100.0, 200.0]  # 2 long positions
        short_pnls = [-50.0, 150.0, -25.0]  # 3 short positions

        for pnl in long_pnls:
            position = Mock()
            position.realized_pnl = pnl
            position.is_long = True
            positions.append(position)

        for pnl in short_pnls:
            position = Mock()
            position.realized_pnl = pnl
            position.is_long = False
            positions.append(position)

        stats = analytics.calculate_side_statistics(positions)

        # Verify long statistics
        long_stats = stats["long"]
        assert long_stats["trade_count"] == 2
        assert long_stats["total_pnl"] == sum(long_pnls)
        assert long_stats["avg_pnl"] == np.mean(long_pnls)
        assert long_stats["win_rate"] == 1.0  # All long trades profitable

        # Verify short statistics
        short_stats = stats["short"]
        assert short_stats["trade_count"] == 3
        assert short_stats["total_pnl"] == sum(short_pnls)
        assert short_stats["avg_pnl"] == np.mean(short_pnls)
        assert short_stats["win_rate"] == 1 / 3  # 1 out of 3 short trades profitable

    def test_calculate_side_statistics_empty_list(self, analytics):
        """Test side statistics with empty position list."""
        stats = analytics.calculate_side_statistics([])
        assert stats["long"]["trade_count"] == 0
        assert stats["short"]["trade_count"] == 0

    def test_calculate_position_size_stats(self, analytics):
        """Test position size statistics calculation."""
        positions = []
        sizes = [100, 200, 150, 250, 175]

        for size in sizes:
            position = Mock()
            position.quantity = size
            positions.append(position)

        stats = analytics.calculate_position_size_stats(positions)

        assert stats["avg_size"] == np.mean(sizes)
        assert stats["max_size"] == max(sizes)
        assert stats["min_size"] == min(sizes)
        assert "std_size" in stats

    def test_calculate_position_size_stats_empty_list(self, analytics):
        """Test position size stats with empty position list."""
        stats = analytics.calculate_position_size_stats([])
        assert stats["avg_size"] == 0.0
        assert stats["max_size"] == 0.0
        assert stats["min_size"] == 0.0

    def test_get_largest_positions(self, analytics):
        """Test getting largest positions by absolute PnL."""
        positions = []
        pnls = [100.0, -250.0, 50.0, 300.0, -75.0]  # Should sort by abs value

        for i, pnl in enumerate(pnls):
            position = Mock()
            position.realized_pnl = pnl
            position.instrument_id = Mock()
            position.instrument_id.__str__ = Mock(return_value=f"STOCK-{i}")
            position.quantity = 100
            position.is_long = pnl > 0
            positions.append(position)

        largest = analytics.get_largest_positions(positions, limit=3)

        assert len(largest) == 3
        # Should be sorted by absolute value: 300, -250, 100
        assert largest[0]["realized_pnl"] == 300.0
        assert largest[1]["realized_pnl"] == -250.0
        assert largest[2]["realized_pnl"] == 100.0

    def test_get_largest_positions_empty_list(self, analytics):
        """Test getting largest positions with empty list."""
        largest = analytics.get_largest_positions([])
        assert largest == []

    def test_calculate_performance_attribution(self, analytics):
        """Test performance attribution by instrument and side."""
        positions = []

        # Create positions for multiple instruments
        instruments_data = [
            ("AAPL", 100.0, True),
            ("AAPL", -50.0, False),
            ("GOOGL", 200.0, True),
            ("GOOGL", 150.0, True),
        ]

        for instrument, pnl, is_long in instruments_data:
            position = Mock()
            position.realized_pnl = pnl
            position.instrument_id = Mock()
            position.instrument_id.__str__ = Mock(return_value=instrument)
            position.is_long = is_long
            positions.append(position)

        attribution = analytics.calculate_performance_attribution(positions)

        # Verify structure
        assert "by_instrument" in attribution
        assert "by_side" in attribution
        assert "total_realized_pnl" in attribution

        # Verify instrument attribution
        assert "AAPL" in attribution["by_instrument"]
        assert "GOOGL" in attribution["by_instrument"]

        aapl_data = attribution["by_instrument"]["AAPL"]
        assert aapl_data["total_pnl"] == 50.0  # 100 + (-50)
        assert aapl_data["trade_count"] == 2

        googl_data = attribution["by_instrument"]["GOOGL"]
        assert googl_data["total_pnl"] == 350.0  # 200 + 150
        assert googl_data["trade_count"] == 2

        # Verify side attribution
        assert "LONG" in attribution["by_side"]
        assert "SHORT" in attribution["by_side"]

        assert attribution["total_realized_pnl"] == 400.0  # 100 - 50 + 200 + 150

    def test_calculate_performance_attribution_empty_list(self, analytics):
        """Test performance attribution with empty position list."""
        attribution = analytics.calculate_performance_attribution([])
        assert attribution["by_instrument"] == {}
        assert attribution["by_side"] == {}
        assert attribution["total_realized_pnl"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
