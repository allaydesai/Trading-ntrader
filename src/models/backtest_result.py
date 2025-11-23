"""Enhanced backtest result models with Nautilus Trader metrics."""

from typing import Any, Dict, Optional
from uuid import uuid4


class BacktestResult:
    """
    Enhanced container for backtest results with full Nautilus Trader metrics.

    Includes basic trade statistics, advanced risk metrics (Sharpe, Sortino, etc.),
    and comprehensive performance analytics from Nautilus Trader's PortfolioAnalyzer.
    """

    def __init__(
        self,
        # Basic metrics
        total_return: float = 0.0,
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        largest_win: float = 0.0,
        largest_loss: float = 0.0,
        final_balance: float = 0.0,
        result_id: str | None = None,
        # Advanced metrics from Nautilus Trader - Returns Stats
        sharpe_ratio: Optional[float] = None,
        sortino_ratio: Optional[float] = None,
        volatility: Optional[float] = None,
        profit_factor: Optional[float] = None,
        risk_return_ratio: Optional[float] = None,
        avg_return: Optional[float] = None,
        avg_win_return: Optional[float] = None,
        avg_loss_return: Optional[float] = None,
        # Advanced metrics from Nautilus Trader - PnL Stats
        total_pnl: Optional[float] = None,
        total_pnl_percentage: Optional[float] = None,
        expectancy: Optional[float] = None,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
        max_winner: Optional[float] = None,
        max_loser: Optional[float] = None,
        min_winner: Optional[float] = None,
        min_loser: Optional[float] = None,
        # Metrics NOT provided by Nautilus Trader (need custom calculation)
        max_drawdown: Optional[float] = None,
        max_drawdown_duration_days: Optional[float] = None,
        cagr: Optional[float] = None,
        calmar_ratio: Optional[float] = None,
        # Additional performance metrics
        total_fees: Optional[float] = None,
        total_commissions: Optional[float] = None,
    ):
        """
        Initialize backtest results with all available metrics.

        Args:
            total_return: Total return amount (not percentage)
            total_trades: Total number of trades executed
            winning_trades: Number of profitable trades
            losing_trades: Number of losing trades
            largest_win: Largest winning trade amount
            largest_loss: Largest losing trade amount (negative)
            final_balance: Final account balance
            result_id: Unique identifier for this result

            # Returns-based metrics (from get_performance_stats_returns)
            sharpe_ratio: Risk-adjusted return metric (annualized, 252 days)
            sortino_ratio: Downside risk-adjusted return metric (252 days)
            volatility: Returns standard deviation (annualized, 252 days)
            profit_factor: Gross profit / gross loss ratio
            risk_return_ratio: Risk-to-return ratio
            avg_return: Average return across all periods
            avg_win_return: Average winning return
            avg_loss_return: Average losing return

            # PnL-based metrics (from get_performance_stats_pnls)
            total_pnl: Total profit and loss amount
            total_pnl_percentage: Total PnL as percentage
            expectancy: Expected profit per trade
            avg_win: Average winning trade amount
            avg_loss: Average losing trade amount
            max_winner: Largest winning trade
            max_loser: Largest losing trade
            min_winner: Smallest winning trade
            min_loser: Smallest losing trade

            # Metrics requiring custom calculation
            max_drawdown: Maximum peak-to-trough decline (as decimal)
            max_drawdown_duration_days: Duration of max drawdown in days
            cagr: Compound annual growth rate
            calmar_ratio: Return over max drawdown ratio

            # Cost metrics
            total_fees: Total fees paid
            total_commissions: Total commissions paid
        """
        self.result_id = result_id or str(uuid4())

        # Basic metrics
        self.total_return = total_return
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.losing_trades = losing_trades
        self.largest_win = largest_win
        self.largest_loss = largest_loss
        self.final_balance = final_balance

        # Returns-based metrics (from get_performance_stats_returns)
        self.sharpe_ratio = sharpe_ratio
        self.sortino_ratio = sortino_ratio
        self.volatility = volatility
        self.profit_factor = profit_factor
        self.risk_return_ratio = risk_return_ratio
        self.avg_return = avg_return
        self.avg_win_return = avg_win_return
        self.avg_loss_return = avg_loss_return

        # PnL-based metrics (from get_performance_stats_pnls)
        self.total_pnl = total_pnl
        self.total_pnl_percentage = total_pnl_percentage
        self.expectancy = expectancy
        self.avg_win = avg_win
        self.avg_loss = avg_loss
        self.max_winner = max_winner
        self.max_loser = max_loser
        self.min_winner = min_winner
        self.min_loser = min_loser

        # Metrics requiring custom calculation
        self.max_drawdown = max_drawdown
        self.max_drawdown_duration_days = max_drawdown_duration_days
        self.cagr = cagr
        self.calmar_ratio = calmar_ratio

        # Cost metrics
        self.total_fees = total_fees
        self.total_commissions = total_commissions

    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert backtest result to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the backtest result
        """
        return {
            "result_id": self.result_id,
            # Basic metrics
            "total_return": self.total_return,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "final_balance": self.final_balance,
            "win_rate": self.win_rate,
            # Returns-based metrics
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "volatility": self.volatility,
            "profit_factor": self.profit_factor,
            "risk_return_ratio": self.risk_return_ratio,
            "avg_return": self.avg_return,
            "avg_win_return": self.avg_win_return,
            "avg_loss_return": self.avg_loss_return,
            # PnL-based metrics
            "total_pnl": self.total_pnl,
            "total_pnl_percentage": self.total_pnl_percentage,
            "expectancy": self.expectancy,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "max_winner": self.max_winner,
            "max_loser": self.max_loser,
            "min_winner": self.min_winner,
            "min_loser": self.min_loser,
            # Custom calculated metrics
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration_days": self.max_drawdown_duration_days,
            "cagr": self.cagr,
            "calmar_ratio": self.calmar_ratio,
            # Cost metrics
            "total_fees": self.total_fees,
            "total_commissions": self.total_commissions,
        }

    def __str__(self) -> str:
        """String representation of results."""
        sharpe_str = f"{self.sharpe_ratio:.2f}" if self.sharpe_ratio is not None else "N/A"
        return (
            f"BacktestResult("
            f"total_return={self.total_return:.2f}, "
            f"total_trades={self.total_trades}, "
            f"win_rate={self.win_rate:.1f}%, "
            f"sharpe={sharpe_str})"
        )
