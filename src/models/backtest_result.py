"""Backtest result models."""

from typing import Dict, Any
from uuid import uuid4


class BacktestResult:
    """Simple container for backtest results."""

    def __init__(
        self,
        total_return: float = 0.0,
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        largest_win: float = 0.0,
        largest_loss: float = 0.0,
        final_balance: float = 0.0,
        result_id: str | None = None,
    ):
        """Initialize backtest results."""
        self.result_id = result_id or str(uuid4())
        self.total_return = total_return
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.losing_trades = losing_trades
        self.largest_win = largest_win
        self.largest_loss = largest_loss
        self.final_balance = final_balance

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
            "total_return": self.total_return,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "final_balance": self.final_balance,
            "win_rate": self.win_rate,
        }

    def __str__(self) -> str:
        """String representation of results."""
        return (
            f"BacktestResult(total_return={self.total_return:.2f}, "
            f"total_trades={self.total_trades}, win_rate={self.win_rate:.1f}%)"
        )
