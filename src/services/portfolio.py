"""Portfolio tracking and analysis service using Nautilus Trader framework."""

from typing import Dict, List, Optional, Any, Union
from decimal import Decimal
from datetime import datetime
import pandas as pd
import numpy as np

from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.position import Position
from nautilus_trader.model.identifiers import InstrumentId

from src.models.trade import TradeModel


class PortfolioService:
    """
    Portfolio tracking and analysis service.

    This service provides comprehensive portfolio monitoring capabilities
    using Nautilus Trader's Portfolio and Cache systems.
    """

    def __init__(self, portfolio: Portfolio, cache: Cache):
        """
        Initialize the portfolio service.

        Args:
            portfolio: Nautilus Portfolio instance
            cache: Nautilus Cache instance
        """
        self.portfolio = portfolio
        self.cache = cache

    def get_current_state(self) -> Dict[str, Any]:
        """
        Get current portfolio state snapshot.

        Returns:
            Dictionary containing current portfolio metrics
        """
        try:
            # Get basic portfolio metrics
            total_pnl = self.portfolio.total_pnl()
            unrealized_pnls = self.portfolio.unrealized_pnls()
            realized_pnls = self.portfolio.realized_pnls()
            net_exposures = self.portfolio.net_exposures()

            # Aggregate metrics
            total_unrealized = sum(unrealized_pnls.values()) if unrealized_pnls else 0.0
            total_realized = sum(realized_pnls.values()) if realized_pnls else 0.0
            total_net_exposure = sum(net_exposures.values()) if net_exposures else 0.0

            # Get position counts
            open_positions = self.cache.positions_open()
            closed_positions = self.cache.positions_closed()

            return {
                'timestamp': pd.Timestamp.now(),
                'total_pnl': float(total_pnl),
                'unrealized_pnl': float(total_unrealized),
                'realized_pnl': float(total_realized),
                'net_exposure': float(total_net_exposure),
                'open_positions': len(open_positions) if open_positions else 0,
                'closed_positions': len(closed_positions) if closed_positions else 0,
                'total_positions': len(open_positions or []) + len(closed_positions or []),
                'is_flat': self.portfolio.is_completely_flat(),
                'account_balances': self._get_account_balances(),
                'position_count_by_instrument': self._get_position_count_by_instrument()
            }

        except Exception as e:
            # Return safe state if portfolio access fails
            return {
                'timestamp': pd.Timestamp.now(),
                'total_pnl': 0.0,
                'unrealized_pnl': 0.0,
                'realized_pnl': 0.0,
                'net_exposure': 0.0,
                'open_positions': 0,
                'closed_positions': 0,
                'total_positions': 0,
                'is_flat': True,
                'error': str(e),
                'account_balances': {},
                'position_count_by_instrument': {}
            }

    def get_equity_curve(self, start_date: Optional[datetime] = None) -> pd.DataFrame:
        """
        Generate equity curve from portfolio history.

        Args:
            start_date: Optional start date for equity curve

        Returns:
            DataFrame with timestamp index and equity/pnl columns
        """
        try:
            # Get position snapshots from cache
            # Note: In a real implementation, this would use Nautilus position snapshots
            # For now, we'll create a basic structure from closed positions

            closed_positions = self.cache.positions_closed()
            if not closed_positions:
                return pd.DataFrame(columns=['timestamp', 'equity', 'cumulative_pnl', 'daily_pnl'])

            # Sort positions by closed time
            sorted_positions = sorted(
                [p for p in closed_positions if hasattr(p, 'closed_time') and p.closed_time],
                key=lambda x: x.closed_time
            )

            if not sorted_positions:
                return pd.DataFrame(columns=['timestamp', 'equity', 'cumulative_pnl', 'daily_pnl'])

            # Build equity curve
            equity_data = []
            cumulative_pnl = 0.0
            initial_capital = 100000.0  # Assume $100k starting capital

            for position in sorted_positions:
                if hasattr(position, 'realized_pnl') and position.realized_pnl is not None:
                    cumulative_pnl += float(position.realized_pnl)

                    equity_data.append({
                        'timestamp': position.closed_time,
                        'equity': initial_capital + cumulative_pnl,
                        'cumulative_pnl': cumulative_pnl,
                        'daily_pnl': float(position.realized_pnl)
                    })

            if not equity_data:
                return pd.DataFrame(columns=['timestamp', 'equity', 'cumulative_pnl', 'daily_pnl'])

            df = pd.DataFrame(equity_data)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.set_index('timestamp').sort_index()

            # Filter by start date if provided
            if start_date:
                df = df[df.index >= start_date]

            return df

        except Exception as e:
            # Return empty DataFrame on error
            return pd.DataFrame(columns=['timestamp', 'equity', 'cumulative_pnl', 'daily_pnl'])

    def get_position_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive position summary.

        Returns:
            Dictionary containing position analytics
        """
        try:
            open_positions = self.cache.positions_open() or []
            closed_positions = self.cache.positions_closed() or []

            # Basic counts
            total_positions = len(open_positions) + len(closed_positions)

            # Instrument analysis
            instruments_traded = set()
            for position in closed_positions + open_positions:
                if hasattr(position, 'instrument_id'):
                    instruments_traded.add(str(position.instrument_id))

            # Trade duration analysis
            avg_duration = self._calculate_avg_duration(closed_positions)

            # PnL analysis
            pnl_stats = self._calculate_pnl_statistics(closed_positions)

            # Side analysis
            side_stats = self._calculate_side_statistics(closed_positions)

            return {
                'open_positions': len(open_positions),
                'closed_positions': len(closed_positions),
                'total_positions': total_positions,
                'instruments_traded': len(instruments_traded),
                'instruments_list': list(instruments_traded),
                'avg_trade_duration_hours': avg_duration,
                'position_sizes': self._calculate_position_size_stats(closed_positions),
                'pnl_statistics': pnl_stats,
                'side_statistics': side_stats,
                'largest_positions': self._get_largest_positions(closed_positions)
            }

        except Exception as e:
            return {
                'open_positions': 0,
                'closed_positions': 0,
                'total_positions': 0,
                'instruments_traded': 0,
                'instruments_list': [],
                'avg_trade_duration_hours': None,
                'error': str(e)
            }

    def get_trades_as_models(self, include_open: bool = True) -> List[TradeModel]:
        """
        Convert Nautilus positions to TradeModel instances.

        Args:
            include_open: Whether to include open positions

        Returns:
            List of TradeModel instances
        """
        trades = []

        try:
            # Get closed positions
            closed_positions = self.cache.positions_closed() or []
            for position in closed_positions:
                try:
                    trade = TradeModel.from_nautilus_position(position)
                    trades.append(trade)
                except Exception:
                    # Skip invalid positions
                    continue

            # Optionally include open positions
            if include_open:
                open_positions = self.cache.positions_open() or []
                for position in open_positions:
                    try:
                        trade = TradeModel.from_nautilus_position(position)
                        trades.append(trade)
                    except Exception:
                        # Skip invalid positions
                        continue

        except Exception as e:
            # Log error but return what we have
            pass

        return trades

    def get_performance_attribution(self) -> Dict[str, Any]:
        """
        Calculate performance attribution by instrument and strategy.

        Returns:
            Dictionary containing performance attribution metrics
        """
        try:
            closed_positions = self.cache.positions_closed() or []

            # Group by instrument
            by_instrument = {}
            by_side = {'LONG': [], 'SHORT': []}

            for position in closed_positions:
                if not hasattr(position, 'realized_pnl') or position.realized_pnl is None:
                    continue

                instrument = str(position.instrument_id) if hasattr(position, 'instrument_id') else 'UNKNOWN'
                pnl = float(position.realized_pnl)

                if instrument not in by_instrument:
                    by_instrument[instrument] = {
                        'total_pnl': 0.0,
                        'trade_count': 0,
                        'winning_trades': 0,
                        'avg_pnl': 0.0
                    }

                by_instrument[instrument]['total_pnl'] += pnl
                by_instrument[instrument]['trade_count'] += 1
                if pnl > 0:
                    by_instrument[instrument]['winning_trades'] += 1

                # Side analysis
                side = 'LONG' if hasattr(position, 'is_long') and position.is_long else 'SHORT'
                by_side[side].append(pnl)

            # Calculate averages
            for instrument_data in by_instrument.values():
                if instrument_data['trade_count'] > 0:
                    instrument_data['avg_pnl'] = instrument_data['total_pnl'] / instrument_data['trade_count']
                    instrument_data['win_rate'] = instrument_data['winning_trades'] / instrument_data['trade_count']

            # Side statistics
            side_stats = {}
            for side, pnls in by_side.items():
                if pnls:
                    side_stats[side] = {
                        'total_pnl': sum(pnls),
                        'trade_count': len(pnls),
                        'avg_pnl': np.mean(pnls),
                        'win_rate': len([p for p in pnls if p > 0]) / len(pnls)
                    }

            return {
                'by_instrument': by_instrument,
                'by_side': side_stats,
                'total_realized_pnl': sum(float(p.realized_pnl) for p in closed_positions
                                       if hasattr(p, 'realized_pnl') and p.realized_pnl is not None)
            }

        except Exception as e:
            return {
                'by_instrument': {},
                'by_side': {},
                'total_realized_pnl': 0.0,
                'error': str(e)
            }

    def _get_account_balances(self) -> Dict[str, float]:
        """Get account balances from portfolio."""
        try:
            # In a real implementation, this would access portfolio.account.balances
            # For now, return a basic structure
            return {
                'USD': 100000.0,  # Placeholder
                'total_equity': float(self.portfolio.total_pnl()) + 100000.0
            }
        except:
            return {'USD': 100000.0}

    def _get_position_count_by_instrument(self) -> Dict[str, int]:
        """Get position count breakdown by instrument."""
        try:
            all_positions = (self.cache.positions_open() or []) + (self.cache.positions_closed() or [])
            counts = {}

            for position in all_positions:
                if hasattr(position, 'instrument_id'):
                    instrument = str(position.instrument_id)
                    counts[instrument] = counts.get(instrument, 0) + 1

            return counts
        except:
            return {}

    def _calculate_avg_duration(self, positions: List[Position]) -> Optional[float]:
        """Calculate average trade duration in hours."""
        if not positions:
            return None

        durations = []
        for position in positions:
            if (hasattr(position, 'closed_time') and position.closed_time and
                hasattr(position, 'opened_time') and position.opened_time):
                duration = (position.closed_time - position.opened_time).total_seconds() / 3600
                durations.append(duration)

        return sum(durations) / len(durations) if durations else None

    def _calculate_pnl_statistics(self, positions: List[Position]) -> Dict[str, Any]:
        """Calculate PnL statistics from positions."""
        if not positions:
            return {'total_pnl': 0.0, 'avg_pnl': 0.0, 'win_rate': 0.0}

        pnls = []
        for position in positions:
            if hasattr(position, 'realized_pnl') and position.realized_pnl is not None:
                pnls.append(float(position.realized_pnl))

        if not pnls:
            return {'total_pnl': 0.0, 'avg_pnl': 0.0, 'win_rate': 0.0}

        winning_trades = [p for p in pnls if p > 0]

        return {
            'total_pnl': sum(pnls),
            'avg_pnl': np.mean(pnls),
            'win_rate': len(winning_trades) / len(pnls),
            'max_win': max(pnls) if pnls else 0.0,
            'max_loss': min(pnls) if pnls else 0.0,
            'std_pnl': np.std(pnls) if len(pnls) > 1 else 0.0
        }

    def _calculate_side_statistics(self, positions: List[Position]) -> Dict[str, Any]:
        """Calculate statistics by position side (LONG/SHORT)."""
        long_pnls = []
        short_pnls = []

        for position in positions:
            if hasattr(position, 'realized_pnl') and position.realized_pnl is not None:
                pnl = float(position.realized_pnl)
                if hasattr(position, 'is_long') and position.is_long:
                    long_pnls.append(pnl)
                else:
                    short_pnls.append(pnl)

        return {
            'long': {
                'trade_count': len(long_pnls),
                'total_pnl': sum(long_pnls) if long_pnls else 0.0,
                'avg_pnl': np.mean(long_pnls) if long_pnls else 0.0,
                'win_rate': len([p for p in long_pnls if p > 0]) / len(long_pnls) if long_pnls else 0.0
            },
            'short': {
                'trade_count': len(short_pnls),
                'total_pnl': sum(short_pnls) if short_pnls else 0.0,
                'avg_pnl': np.mean(short_pnls) if short_pnls else 0.0,
                'win_rate': len([p for p in short_pnls if p > 0]) / len(short_pnls) if short_pnls else 0.0
            }
        }

    def _calculate_position_size_stats(self, positions: List[Position]) -> Dict[str, Any]:
        """Calculate position size statistics."""
        if not positions:
            return {'avg_size': 0.0, 'max_size': 0.0, 'min_size': 0.0}

        sizes = []
        for position in positions:
            if hasattr(position, 'quantity') and position.quantity is not None:
                sizes.append(abs(float(position.quantity)))

        if not sizes:
            return {'avg_size': 0.0, 'max_size': 0.0, 'min_size': 0.0}

        return {
            'avg_size': np.mean(sizes),
            'max_size': max(sizes),
            'min_size': min(sizes),
            'std_size': np.std(sizes) if len(sizes) > 1 else 0.0
        }

    def _get_largest_positions(self, positions: List[Position], limit: int = 5) -> List[Dict[str, Any]]:
        """Get the largest positions by absolute PnL."""
        if not positions:
            return []

        position_data = []
        for position in positions:
            if hasattr(position, 'realized_pnl') and position.realized_pnl is not None:
                position_data.append({
                    'instrument_id': str(position.instrument_id) if hasattr(position, 'instrument_id') else 'UNKNOWN',
                    'realized_pnl': float(position.realized_pnl),
                    'quantity': float(position.quantity) if hasattr(position, 'quantity') else 0.0,
                    'side': 'LONG' if hasattr(position, 'is_long') and position.is_long else 'SHORT'
                })

        # Sort by absolute PnL
        position_data.sort(key=lambda x: abs(x['realized_pnl']), reverse=True)

        return position_data[:limit]