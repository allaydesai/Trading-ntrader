"""Portfolio tracking and analysis service using Nautilus Trader framework."""

from typing import Dict, List, Optional, Any
from datetime import datetime
import pandas as pd

from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.cache.cache import Cache

from src.models.trade import TradeModel
from src.services.portfolio_analytics import PortfolioAnalytics


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
        self.analytics = PortfolioAnalytics()

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
                "timestamp": pd.Timestamp.now(),
                "total_pnl": float(total_pnl),
                "unrealized_pnl": float(total_unrealized),
                "realized_pnl": float(total_realized),
                "net_exposure": float(total_net_exposure),
                "open_positions": len(open_positions) if open_positions else 0,
                "closed_positions": len(closed_positions) if closed_positions else 0,
                "total_positions": len(open_positions or [])
                + len(closed_positions or []),
                "is_flat": self.portfolio.is_completely_flat(),
                "account_balances": self._get_account_balances(),
                "position_count_by_instrument": self._get_position_count_by_instrument(),
            }

        except Exception as e:
            # Return safe state if portfolio access fails
            return {
                "timestamp": pd.Timestamp.now(),
                "total_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "net_exposure": 0.0,
                "open_positions": 0,
                "closed_positions": 0,
                "total_positions": 0,
                "is_flat": True,
                "error": str(e),
                "account_balances": {},
                "position_count_by_instrument": {},
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
            closed_positions = self.cache.positions_closed()
            if not closed_positions:
                return pd.DataFrame(
                    columns=["timestamp", "equity", "cumulative_pnl", "daily_pnl"]
                )

            # Sort positions by closed time
            sorted_positions = sorted(
                [
                    p
                    for p in closed_positions
                    if hasattr(p, "closed_time") and p.closed_time
                ],
                key=lambda x: x.closed_time,
            )

            if not sorted_positions:
                return pd.DataFrame(
                    columns=["timestamp", "equity", "cumulative_pnl", "daily_pnl"]
                )

            # Build equity curve
            equity_data = []
            cumulative_pnl = 0.0
            initial_capital = 100000.0  # Assume $100k starting capital

            for position in sorted_positions:
                if (
                    hasattr(position, "realized_pnl")
                    and position.realized_pnl is not None
                ):
                    cumulative_pnl += float(position.realized_pnl)

                    equity_data.append(
                        {
                            "timestamp": position.closed_time,
                            "equity": initial_capital + cumulative_pnl,
                            "cumulative_pnl": cumulative_pnl,
                            "daily_pnl": float(position.realized_pnl),
                        }
                    )

            if not equity_data:
                return pd.DataFrame(
                    columns=["timestamp", "equity", "cumulative_pnl", "daily_pnl"]
                )

            df = pd.DataFrame(equity_data)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp").sort_index()

            # Filter by start date if provided
            if start_date:
                df = df[df.index >= start_date]

            return df

        except Exception:
            # Return empty DataFrame on error
            return pd.DataFrame(
                columns=["timestamp", "equity", "cumulative_pnl", "daily_pnl"]
            )

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
                if hasattr(position, "instrument_id"):
                    instruments_traded.add(str(position.instrument_id))

            # Delegate analytics calculations to analytics module
            avg_duration = self.analytics.calculate_avg_duration(closed_positions)
            pnl_stats = self.analytics.calculate_pnl_statistics(closed_positions)
            side_stats = self.analytics.calculate_side_statistics(closed_positions)
            position_sizes = self.analytics.calculate_position_size_stats(
                closed_positions
            )
            largest_positions = self.analytics.get_largest_positions(closed_positions)

            return {
                "open_positions": len(open_positions),
                "closed_positions": len(closed_positions),
                "total_positions": total_positions,
                "instruments_traded": len(instruments_traded),
                "instruments_list": list(instruments_traded),
                "avg_trade_duration_hours": avg_duration,
                "position_sizes": position_sizes,
                "pnl_statistics": pnl_stats,
                "side_statistics": side_stats,
                "largest_positions": largest_positions,
            }

        except Exception as e:
            return {
                "open_positions": 0,
                "closed_positions": 0,
                "total_positions": 0,
                "instruments_traded": 0,
                "instruments_list": [],
                "avg_trade_duration_hours": None,
                "error": str(e),
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

        except Exception:
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
            return self.analytics.calculate_performance_attribution(closed_positions)

        except Exception as e:
            return {
                "by_instrument": {},
                "by_side": {},
                "total_realized_pnl": 0.0,
                "error": str(e),
            }

    def _get_account_balances(self) -> Dict[str, float]:
        """Get account balances from portfolio."""
        try:
            # In a real implementation, this would access portfolio.account.balances
            # For now, return a basic structure
            return {
                "USD": 100000.0,  # Placeholder
                "total_equity": float(self.portfolio.total_pnl()) + 100000.0,
            }
        except Exception:
            return {"USD": 100000.0}

    def _get_position_count_by_instrument(self) -> Dict[str, int]:
        """Get position count breakdown by instrument."""
        try:
            all_positions = (self.cache.positions_open() or []) + (
                self.cache.positions_closed() or []
            )
            counts: Dict[str, int] = {}

            for position in all_positions:
                if hasattr(position, "instrument_id"):
                    instrument = str(position.instrument_id)
                    counts[instrument] = counts.get(instrument, 0) + 1

            return counts
        except Exception:
            return {}
