"""Performance calculation service using Nautilus Trader analytics framework."""

from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np

from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.analysis.statistics.sharpe_ratio import SharpeRatio
from nautilus_trader.analysis.statistics.sortino_ratio import SortinoRatio
from nautilus_trader.analysis.statistics.profit_factor import ProfitFactor
from nautilus_trader.analysis.statistics.returns_volatility import ReturnsVolatility
from nautilus_trader.analysis.statistics.returns_avg_win import ReturnsAverageWin
from nautilus_trader.analysis.statistics.returns_avg_loss import ReturnsAverageLoss
from nautilus_trader.analysis.statistic import PortfolioStatistic
from nautilus_trader.portfolio.portfolio import Portfolio


class MaxDrawdown(PortfolioStatistic):
    """Custom maximum drawdown statistic extending Nautilus framework."""

    def calculate_from_returns(self, returns: pd.Series) -> Dict[str, Any]:
        """
        Calculate maximum drawdown with recovery tracking.

        Args:
            returns: Time-indexed returns series

        Returns:
            Dictionary containing max drawdown, date, and recovery info
        """
        if returns.empty:
            return {
                "max_drawdown": 0.0,
                "max_drawdown_date": None,
                "recovery_date": None,
                "recovery_days": None,
            }

        # Calculate cumulative returns and running maximum
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max

        max_dd = drawdown.min()
        max_dd_date = drawdown.idxmin()

        # Recovery analysis
        recovery_date = None
        recovery_days = None
        if max_dd_date in cumulative.index:
            post_dd = cumulative[max_dd_date:]
            recovery_level = running_max.loc[max_dd_date]
            recovery_series = post_dd[post_dd >= recovery_level]
            if not recovery_series.empty:
                recovery_date = recovery_series.index[0]
                recovery_days = (recovery_date - max_dd_date).days

        return {
            "max_drawdown": float(max_dd),
            "max_drawdown_date": max_dd_date,
            "recovery_date": recovery_date,
            "recovery_days": recovery_days,
        }

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Optional[float]:
        """Calculate max drawdown from realized PnLs."""
        if realized_pnls is None or realized_pnls.empty:
            return 0.0

        # Convert PnLs to returns and calculate drawdown
        cumulative_pnl = realized_pnls.cumsum()
        running_max = cumulative_pnl.expanding().max()
        drawdown = cumulative_pnl - running_max

        return float(drawdown.min()) if not drawdown.empty else 0.0


class CalmarRatio(PortfolioStatistic):
    """Custom Calmar Ratio statistic (Annual Return / Max Drawdown)."""

    def calculate_from_returns(self, returns: pd.Series) -> float:
        """
        Calculate Calmar Ratio from returns series.

        Args:
            returns: Time-indexed returns series

        Returns:
            Calmar ratio value
        """
        if returns.empty:
            return 0.0

        # Calculate annualized return
        total_return = (1 + returns).prod() - 1
        periods_per_year = 252  # Assume daily data
        if len(returns) > 0:
            annual_return = (1 + total_return) ** (periods_per_year / len(returns)) - 1
        else:
            annual_return = 0.0

        # Calculate max drawdown
        max_dd_calc = MaxDrawdown()
        max_dd_result = max_dd_calc.calculate_from_returns(returns)
        max_dd = abs(max_dd_result["max_drawdown"])

        # Return Calmar ratio
        return float(annual_return / max_dd) if max_dd > 0 else 0.0

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Optional[float]:
        """Calculate Calmar ratio from realized PnLs."""
        if realized_pnls is None or realized_pnls.empty:
            return 0.0

        # Convert PnLs to simple returns approximation
        # This is a simplified approach - in practice you'd want actual equity curve
        if len(realized_pnls) < 2:
            return 0.0

        total_pnl = realized_pnls.sum()
        initial_capital = 100000  # Assume $100k starting capital
        total_return = total_pnl / initial_capital

        # Annualize the return
        periods_per_year = 252
        annual_return = total_return * (periods_per_year / len(realized_pnls))

        # Calculate max drawdown
        max_dd_calc = MaxDrawdown()
        max_dd = abs(max_dd_calc.calculate_from_realized_pnls(realized_pnls))

        return float(annual_return / max_dd) if max_dd > 0 else 0.0


class WinRate(PortfolioStatistic):
    """Custom Win Rate statistic compatible with Nautilus framework."""

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Optional[float]:
        """
        Calculate win rate from realized PnLs series.

        Args:
            realized_pnls: Series of realized PnL values

        Returns:
            Win rate as a percentage (0.0 to 1.0)
        """
        if realized_pnls is None or realized_pnls.empty:
            return 0.0

        # Calculate winners and losers
        winners = [x for x in realized_pnls if x > 0.0]
        total_trades = len(realized_pnls)

        return len(winners) / max(1, total_trades) if total_trades > 0 else 0.0


class Expectancy(PortfolioStatistic):
    """Custom Expectancy statistic compatible with Nautilus framework."""

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Optional[float]:
        """
        Calculate expectancy from realized PnLs series.

        Args:
            realized_pnls: Series of realized PnL values

        Returns:
            Expected value per trade
        """
        if realized_pnls is None or realized_pnls.empty:
            return 0.0

        return float(realized_pnls.mean())


class PerformanceCalculator:
    """
    Main performance calculation engine using Nautilus Trader analytics framework.

    This class integrates built-in Nautilus statistics with custom statistics
    to provide comprehensive performance metrics for backtests.
    """

    def __init__(self):
        """Initialize the performance calculator with Nautilus analyzer."""
        self.analyzer = PortfolioAnalyzer()
        self._register_statistics()

    def _register_statistics(self):
        """Register all required statistics with the analyzer."""

        # Built-in Nautilus statistics (work with returns)
        built_in_stats = [
            SharpeRatio(),
            SortinoRatio(),
            ProfitFactor(),
            ReturnsVolatility(),
            ReturnsAverageWin(),
            ReturnsAverageLoss(),
        ]

        for stat in built_in_stats:
            self.analyzer.register_statistic(stat)

        # Custom statistics (work with both returns and realized PnLs)
        custom_stats = [MaxDrawdown(), CalmarRatio(), WinRate(), Expectancy()]

        for stat in custom_stats:
            self.analyzer.register_statistic(stat)

    def calculate_metrics(self, portfolio: Portfolio) -> Dict[str, Any]:
        """
        Calculate comprehensive performance metrics from Nautilus Portfolio.

        Args:
            portfolio: Nautilus Portfolio instance

        Returns:
            Dictionary containing all calculated metrics
        """
        # Get basic portfolio metrics
        portfolio_metrics = self._calculate_portfolio_metrics(portfolio)

        # For now, return basic portfolio metrics
        # Full Nautilus integration will be enhanced when we have actual backtest data
        return {
            **portfolio_metrics,
            "calculation_timestamp": pd.Timestamp.now(),
            "analyzer_ready": True,
            "statistics_registered": len(self.analyzer._statistics),
        }

    def calculate_metrics_from_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate metrics from structured data (for testing).

        Args:
            data: Dictionary containing returns series, PnL data, etc.

        Returns:
            Dictionary containing calculated metrics
        """
        metrics = {}

        # Extract returns series if available
        if "return_series" in data and isinstance(data["return_series"], pd.Series):
            returns = data["return_series"]

            # Calculate metrics using our custom statistics
            max_dd_calc = MaxDrawdown()
            max_dd_result = max_dd_calc.calculate_from_returns(returns)

            calmar_calc = CalmarRatio()
            calmar_ratio = calmar_calc.calculate_from_returns(returns)

            # Use Nautilus built-in statistics
            sharpe_calc = SharpeRatio()
            sharpe_ratio = sharpe_calc.calculate_from_returns(returns)

            sortino_calc = SortinoRatio()
            sortino_ratio = sortino_calc.calculate_from_returns(returns)

            volatility_calc = ReturnsVolatility()
            volatility = volatility_calc.calculate_from_returns(returns)

            metrics.update(
                {
                    "sharpe_ratio": sharpe_ratio,
                    "sortino_ratio": sortino_ratio,
                    "volatility": volatility,
                    "max_drawdown": max_dd_result["max_drawdown"],
                    "max_drawdown_date": max_dd_result["max_drawdown_date"],
                    "calmar_ratio": calmar_ratio,
                    "total_return": float((1 + returns).prod() - 1),
                }
            )

        # Add basic portfolio data
        for key in ["total_pnl", "realized_pnl", "unrealized_pnl"]:
            if key in data:
                metrics[key] = data[key]

        # Calculate additional derived metrics
        if "trades" in data:
            trades = data["trades"]
            if trades:
                winning_trades = [t for t in trades if t.get("pnl", 0) > 0]
                losing_trades = [t for t in trades if t.get("pnl", 0) <= 0]

                metrics.update(
                    {
                        "total_trades": len(trades),
                        "winning_trades": len(winning_trades),
                        "losing_trades": len(losing_trades),
                        "win_rate": len(winning_trades) / len(trades)
                        if trades
                        else 0.0,
                        "avg_win": np.mean([t["pnl"] for t in winning_trades])
                        if winning_trades
                        else 0.0,
                        "avg_loss": np.mean([t["pnl"] for t in losing_trades])
                        if losing_trades
                        else 0.0,
                        "largest_win": max([t["pnl"] for t in winning_trades])
                        if winning_trades
                        else 0.0,
                        "largest_loss": min([t["pnl"] for t in losing_trades])
                        if losing_trades
                        else 0.0,
                    }
                )

        metrics["calculation_timestamp"] = pd.Timestamp.now()
        return metrics

    def calculate_metrics_from_backtest_result(
        self, backtest_result: Any
    ) -> Dict[str, Any]:
        """
        Calculate metrics from a backtest result.

        Args:
            backtest_result: Backtest result containing trades and equity data

        Returns:
            Dictionary containing comprehensive metrics
        """
        # This will be implemented when we integrate with actual backtest results
        # For now, return a placeholder structure
        return {
            "total_return": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "calculation_timestamp": pd.Timestamp.now(),
            "status": "placeholder_implementation",
        }

    def _calculate_portfolio_metrics(self, portfolio: Portfolio) -> Dict[str, Any]:
        """Calculate basic metrics from Nautilus Portfolio."""
        try:
            return {
                "total_pnl": float(portfolio.total_pnl()),
                "unrealized_pnl": sum(portfolio.unrealized_pnls().values())
                if portfolio.unrealized_pnls()
                else 0.0,
                "realized_pnl": sum(portfolio.realized_pnls().values())
                if portfolio.realized_pnls()
                else 0.0,
                "net_exposure": sum(portfolio.net_exposures().values())
                if portfolio.net_exposures()
                else 0.0,
            }
        except Exception as e:
            # Return safe defaults if portfolio access fails
            return {
                "total_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "net_exposure": 0.0,
                "error": str(e),
            }

    def get_registered_statistics(self) -> List[str]:
        """Get list of registered statistic names."""
        return list(self.analyzer._statistics.keys())

    def calculate_custom_metrics(self, returns: pd.Series) -> Dict[str, Any]:
        """
        Calculate custom metrics from returns series.

        Args:
            returns: Time-indexed returns series

        Returns:
            Dictionary of custom metric calculations
        """
        if returns.empty:
            return {}

        max_dd = MaxDrawdown()
        max_dd_result = max_dd.calculate_from_returns(returns)

        calmar = CalmarRatio()
        calmar_ratio = calmar.calculate_from_returns(returns)

        return {
            "max_drawdown": max_dd_result["max_drawdown"],
            "max_drawdown_date": max_dd_result["max_drawdown_date"],
            "recovery_date": max_dd_result["recovery_date"],
            "recovery_days": max_dd_result["recovery_days"],
            "calmar_ratio": calmar_ratio,
        }
