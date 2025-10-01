"""Test suite for PortfolioService with Nautilus integration."""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
import pandas as pd
import numpy as np

from src.services.portfolio import PortfolioService
from src.models.trade import TradeModel


class TestPortfolioService:
    """Test PortfolioService functionality."""

    def create_mock_portfolio(self):
        """Create a mock Nautilus Portfolio for testing."""
        portfolio = Mock()

        # Mock portfolio methods
        portfolio.total_pnl.return_value = 5000.0
        portfolio.unrealized_pnls.return_value = {'AAPL': 1000.0, 'GOOGL': 500.0}
        portfolio.realized_pnls.return_value = {'AAPL': 3000.0, 'MSFT': 500.0}
        portfolio.net_exposures.return_value = {'AAPL': 150000.0, 'GOOGL': 100000.0}
        portfolio.is_completely_flat.return_value = False

        return portfolio

    def create_mock_cache(self, num_open=2, num_closed=5):
        """Create a mock Nautilus Cache for testing."""
        cache = Mock()

        # Create mock open positions
        open_positions = []
        for i in range(num_open):
            position = self.create_mock_position(
                position_id=f"open-{i}",
                instrument_id=f"AAPL.NASDAQ",
                is_closed=False
            )
            open_positions.append(position)

        # Create mock closed positions
        closed_positions = []
        for i in range(num_closed):
            position = self.create_mock_position(
                position_id=f"closed-{i}",
                instrument_id=f"STOCK-{i}.NASDAQ",
                is_closed=True,
                realized_pnl=100.0 * (i + 1) * (1 if i % 2 == 0 else -1)  # Mix of wins/losses
            )
            closed_positions.append(position)

        cache.positions_open.return_value = open_positions
        cache.positions_closed.return_value = closed_positions

        return cache

    def create_mock_position(self, position_id="test", instrument_id="AAPL.NASDAQ",
                           is_closed=False, realized_pnl=None, is_long=True):
        """Create a mock Nautilus Position."""
        position = Mock()

        # Basic properties
        position.id = Mock()
        position.id.__str__ = Mock(return_value=position_id)
        position.instrument_id = Mock()
        position.instrument_id.__str__ = Mock(return_value=instrument_id)

        # Timestamps
        base_time = datetime(2024, 1, 15, 10, 30)
        position.opened_time = base_time
        position.is_closed = is_closed
        position.closed_time = base_time + timedelta(hours=2) if is_closed else None

        # Position data
        position.avg_px_open = 150.50
        position.avg_px_close = 155.75 if is_closed else None
        position.quantity = 100 if is_long else -100
        position.is_long = is_long

        # Financial data
        position.commission = 5.50
        position.realized_pnl = realized_pnl

        return position

    def test_portfolio_service_initialization(self):
        """Test PortfolioService initialization."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache()

        service = PortfolioService(portfolio, cache)

        assert service.portfolio == portfolio
        assert service.cache == cache

    def test_get_current_state(self):
        """Test getting current portfolio state."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache(num_open=3, num_closed=7)

        service = PortfolioService(portfolio, cache)
        state = service.get_current_state()

        # Verify basic structure
        assert 'timestamp' in state
        assert 'total_pnl' in state
        assert 'unrealized_pnl' in state
        assert 'realized_pnl' in state
        assert 'net_exposure' in state
        assert 'open_positions' in state
        assert 'closed_positions' in state

        # Verify values
        assert state['total_pnl'] == 5000.0
        assert state['unrealized_pnl'] == 1500.0  # 1000 + 500
        assert state['realized_pnl'] == 3500.0    # 3000 + 500
        assert state['net_exposure'] == 250000.0  # 150000 + 100000
        assert state['open_positions'] == 3
        assert state['closed_positions'] == 7
        assert state['total_positions'] == 10
        assert state['is_flat'] is False

    def test_get_current_state_error_handling(self):
        """Test error handling in get_current_state."""
        portfolio = Mock()
        portfolio.total_pnl.side_effect = Exception("Portfolio error")
        cache = Mock()

        service = PortfolioService(portfolio, cache)
        state = service.get_current_state()

        # Should return safe defaults
        assert state['total_pnl'] == 0.0
        assert state['open_positions'] == 0
        assert state['is_flat'] is True
        assert 'error' in state

    def test_get_equity_curve(self):
        """Test equity curve generation."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache(num_closed=3)

        service = PortfolioService(portfolio, cache)
        equity_curve = service.get_equity_curve()

        # Should return DataFrame with expected columns
        assert isinstance(equity_curve, pd.DataFrame)
        expected_columns = ['equity', 'cumulative_pnl', 'daily_pnl']
        for col in expected_columns:
            if not equity_curve.empty:
                assert col in equity_curve.columns

    def test_get_equity_curve_empty_positions(self):
        """Test equity curve with no closed positions."""
        portfolio = self.create_mock_portfolio()
        cache = Mock()
        cache.positions_closed.return_value = []

        service = PortfolioService(portfolio, cache)
        equity_curve = service.get_equity_curve()

        # Should return empty DataFrame with correct columns
        assert isinstance(equity_curve, pd.DataFrame)
        assert equity_curve.empty
        expected_columns = ['timestamp', 'equity', 'cumulative_pnl', 'daily_pnl']
        for col in expected_columns:
            assert col in equity_curve.columns

    def test_get_position_summary(self):
        """Test position summary calculation."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache(num_open=2, num_closed=8)

        service = PortfolioService(portfolio, cache)
        summary = service.get_position_summary()

        # Verify structure
        assert 'open_positions' in summary
        assert 'closed_positions' in summary
        assert 'total_positions' in summary
        assert 'instruments_traded' in summary
        assert 'instruments_list' in summary
        assert 'pnl_statistics' in summary
        assert 'side_statistics' in summary

        # Verify values
        assert summary['open_positions'] == 2
        assert summary['closed_positions'] == 8
        assert summary['total_positions'] == 10
        assert summary['instruments_traded'] > 0  # Should have multiple instruments

        # Verify PnL statistics structure
        pnl_stats = summary['pnl_statistics']
        assert 'total_pnl' in pnl_stats
        assert 'avg_pnl' in pnl_stats
        assert 'win_rate' in pnl_stats

    def test_get_trades_as_models(self):
        """Test converting Nautilus positions to TradeModel instances."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache(num_open=1, num_closed=3)

        service = PortfolioService(portfolio, cache)

        # Test with open positions included
        trades_with_open = service.get_trades_as_models(include_open=True)
        assert len(trades_with_open) == 4  # 1 open + 3 closed

        # Test without open positions
        trades_closed_only = service.get_trades_as_models(include_open=False)
        assert len(trades_closed_only) == 3  # Only closed

        # Verify all are TradeModel instances
        for trade in trades_with_open:
            assert isinstance(trade, TradeModel)

    def test_get_performance_attribution(self):
        """Test performance attribution calculation."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache(num_closed=5)

        service = PortfolioService(portfolio, cache)
        attribution = service.get_performance_attribution()

        # Verify structure
        assert 'by_instrument' in attribution
        assert 'by_side' in attribution
        assert 'total_realized_pnl' in attribution

        # Verify instrument breakdown
        by_instrument = attribution['by_instrument']
        for instrument_data in by_instrument.values():
            assert 'total_pnl' in instrument_data
            assert 'trade_count' in instrument_data
            assert 'winning_trades' in instrument_data
            assert 'avg_pnl' in instrument_data
            assert 'win_rate' in instrument_data

        # Verify side breakdown
        by_side = attribution['by_side']
        for side_data in by_side.values():
            assert 'total_pnl' in side_data
            assert 'trade_count' in side_data
            assert 'avg_pnl' in side_data
            assert 'win_rate' in side_data

    def test_portfolio_service_with_no_data(self):
        """Test PortfolioService with empty portfolio."""
        portfolio = Mock()
        portfolio.total_pnl.return_value = 0.0
        portfolio.unrealized_pnls.return_value = {}
        portfolio.realized_pnls.return_value = {}
        portfolio.net_exposures.return_value = {}
        portfolio.is_completely_flat.return_value = True

        cache = Mock()
        cache.positions_open.return_value = []
        cache.positions_closed.return_value = []

        service = PortfolioService(portfolio, cache)

        # Test all methods with empty data
        state = service.get_current_state()
        assert state['total_pnl'] == 0.0
        assert state['open_positions'] == 0
        assert state['is_flat'] is True

        summary = service.get_position_summary()
        assert summary['total_positions'] == 0

        trades = service.get_trades_as_models()
        assert len(trades) == 0

        attribution = service.get_performance_attribution()
        assert attribution['total_realized_pnl'] == 0.0

    def test_avg_duration_calculation(self):
        """Test average trade duration calculation."""
        portfolio = self.create_mock_portfolio()
        cache = self.create_mock_cache(num_closed=3)

        service = PortfolioService(portfolio, cache)

        # Create positions with known durations
        positions = []
        base_time = datetime(2024, 1, 15, 10, 0)

        for i in range(3):
            position = Mock()
            position.opened_time = base_time + timedelta(hours=i)
            position.closed_time = base_time + timedelta(hours=i+2)  # 2 hour duration each
            positions.append(position)

        duration = service._calculate_avg_duration(positions)
        assert duration == 2.0  # 2 hours average

    def test_pnl_statistics_calculation(self):
        """Test PnL statistics calculation."""
        portfolio = self.create_mock_portfolio()
        cache = Mock()

        service = PortfolioService(portfolio, cache)

        # Create positions with known PnLs
        positions = []
        pnls = [100.0, -50.0, 200.0, -25.0, 150.0]  # 3 winners, 2 losers

        for i, pnl in enumerate(pnls):
            position = Mock()
            position.realized_pnl = pnl
            positions.append(position)

        stats = service._calculate_pnl_statistics(positions)

        assert stats['total_pnl'] == sum(pnls)  # 375.0
        assert stats['avg_pnl'] == np.mean(pnls)  # 75.0
        assert stats['win_rate'] == 0.6  # 3/5 = 60%
        assert stats['max_win'] == 200.0
        assert stats['max_loss'] == -50.0

    def test_side_statistics_calculation(self):
        """Test position side statistics calculation."""
        portfolio = self.create_mock_portfolio()
        cache = Mock()

        service = PortfolioService(portfolio, cache)

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

        stats = service._calculate_side_statistics(positions)

        # Verify long statistics
        long_stats = stats['long']
        assert long_stats['trade_count'] == 2
        assert long_stats['total_pnl'] == sum(long_pnls)
        assert long_stats['avg_pnl'] == np.mean(long_pnls)
        assert long_stats['win_rate'] == 1.0  # All long trades profitable

        # Verify short statistics
        short_stats = stats['short']
        assert short_stats['trade_count'] == 3
        assert short_stats['total_pnl'] == sum(short_pnls)
        assert short_stats['avg_pnl'] == np.mean(short_pnls)
        assert short_stats['win_rate'] == 1/3  # 1 out of 3 short trades profitable


class TestPortfolioServiceIntegration:
    """Test PortfolioService integration scenarios."""

    def test_portfolio_service_real_time_updates(self):
        """Test portfolio service handles real-time updates."""
        portfolio = Mock()
        portfolio.unrealized_pnls.return_value = {}
        portfolio.realized_pnls.return_value = {}
        portfolio.net_exposures.return_value = {}
        portfolio.is_completely_flat.return_value = False

        cache = Mock()
        cache.positions_open.return_value = []
        cache.positions_closed.return_value = []

        service = PortfolioService(portfolio, cache)

        # Mock changing portfolio state
        portfolio.total_pnl.return_value = 1000.0
        state1 = service.get_current_state()

        portfolio.total_pnl.return_value = 1500.0
        state2 = service.get_current_state()

        # States should reflect the changes
        assert state1['total_pnl'] == 1000.0
        assert state2['total_pnl'] == 1500.0
        assert state2['timestamp'] >= state1['timestamp']

    def test_portfolio_service_error_resilience(self):
        """Test that PortfolioService is resilient to various errors."""
        portfolio = Mock()
        cache = Mock()

        # Test with methods that raise exceptions
        portfolio.total_pnl.side_effect = Exception("Connection error")
        cache.positions_open.side_effect = Exception("Cache error")

        service = PortfolioService(portfolio, cache)

        # All methods should handle errors gracefully
        state = service.get_current_state()
        assert 'error' in state

        equity_curve = service.get_equity_curve()
        assert isinstance(equity_curve, pd.DataFrame)

        summary = service.get_position_summary()
        assert 'error' in summary or summary['total_positions'] == 0

    def test_portfolio_service_data_consistency(self):
        """Test data consistency across different methods."""
        portfolio = Mock()
        portfolio.total_pnl.return_value = 2500.0
        portfolio.unrealized_pnls.return_value = {'AAPL': 500.0}
        portfolio.realized_pnls.return_value = {'AAPL': 2000.0}
        portfolio.net_exposures.return_value = {'AAPL': 100000.0}
        portfolio.is_completely_flat.return_value = False

        cache = Mock()

        # Create consistent position data
        closed_position = Mock()
        closed_position.realized_pnl = 2000.0  # Should match realized_pnls total
        closed_position.instrument_id = Mock()
        closed_position.instrument_id.__str__ = Mock(return_value="AAPL")
        closed_position.is_long = True

        cache.positions_open.return_value = []
        cache.positions_closed.return_value = [closed_position]

        service = PortfolioService(portfolio, cache)

        # Get data from different methods
        state = service.get_current_state()
        attribution = service.get_performance_attribution()

        # Verify consistency
        assert state['realized_pnl'] == 2000.0
        assert attribution['total_realized_pnl'] == 2000.0

    def test_portfolio_service_large_dataset(self):
        """Test PortfolioService performance with large datasets."""
        portfolio = Mock()
        portfolio.total_pnl.return_value = 50000.0
        portfolio.unrealized_pnls.return_value = {}
        portfolio.realized_pnls.return_value = {}
        portfolio.net_exposures.return_value = {}
        portfolio.is_completely_flat.return_value = False

        cache = Mock()

        # Create large number of positions
        large_position_list = []
        for i in range(1000):
            position = Mock()
            position.id = Mock()
            position.id.__str__ = Mock(return_value=f"pos-{i}")
            position.instrument_id = Mock()
            position.instrument_id.__str__ = Mock(return_value=f"STOCK-{i % 10}")
            position.realized_pnl = 50.0 if i % 2 == 0 else -25.0
            position.is_long = i % 2 == 0
            position.quantity = 100
            position.opened_time = datetime.now() - timedelta(days=i % 30)
            position.closed_time = datetime.now() - timedelta(days=i % 30 - 1)

            # Add required attributes for TradeModel
            position.is_closed = True
            position.avg_px_open = 100.0 + i % 50
            position.avg_px_close = 102.0 + i % 50
            position.commission = 1.0

            large_position_list.append(position)

        cache.positions_open.return_value = []
        cache.positions_closed.return_value = large_position_list

        service = PortfolioService(portfolio, cache)

        # All methods should handle large datasets
        state = service.get_current_state()
        assert state['closed_positions'] == 1000

        summary = service.get_position_summary()
        assert summary['total_positions'] == 1000

        attribution = service.get_performance_attribution()
        assert len(attribution['by_instrument']) == 10  # 10 unique instruments

        trades = service.get_trades_as_models(include_open=False)
        assert len(trades) == 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])