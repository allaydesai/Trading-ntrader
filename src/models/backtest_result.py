"""Enhanced backtest result model with comprehensive analytics."""

from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, Field
import uuid

from src.models.trade import TradeModel


class BacktestMetadata(BaseModel):
    """Metadata about the backtest execution."""

    backtest_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    strategy_name: str
    strategy_type: str
    symbol: str
    start_date: datetime
    end_date: datetime
    parameters: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class EnhancedBacktestResult(BaseModel):
    """
    Comprehensive backtest result with full analytics integration.

    This model extends the basic backtest results with:
    - Nautilus-powered performance metrics
    - Portfolio analytics
    - Enhanced trade tracking
    - Multi-format export support
    """

    # Metadata
    metadata: BacktestMetadata

    # Basic Results (compatible with original BacktestResult)
    total_return: Decimal = Field(default=Decimal("0"))
    total_trades: int = Field(default=0)
    winning_trades: int = Field(default=0)
    losing_trades: int = Field(default=0)
    largest_win: Decimal = Field(default=Decimal("0"))
    largest_loss: Decimal = Field(default=Decimal("0"))
    final_balance: Decimal = Field(default=Decimal("0"))

    # Enhanced Performance Metrics (from PerformanceCalculator)
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    max_drawdown_date: Optional[datetime] = None
    calmar_ratio: Optional[float] = None
    volatility: Optional[float] = None
    profit_factor: Optional[float] = None

    # Portfolio Metrics (from PortfolioService)
    realized_pnl: Decimal = Field(default=Decimal("0"))
    unrealized_pnl: Decimal = Field(default=Decimal("0"))
    total_pnl: Decimal = Field(default=Decimal("0"))
    net_exposure: Decimal = Field(default=Decimal("0"))
    open_positions: int = Field(default=0)
    closed_positions: int = Field(default=0)

    # Trade Analytics
    trades: List[Dict[str, Any]] = Field(default_factory=list)
    avg_win: Optional[Decimal] = None
    avg_loss: Optional[Decimal] = None
    avg_trade_duration_hours: Optional[float] = None
    expectancy: Optional[float] = None

    # Additional Statistics
    instruments_traded: int = Field(default=0)
    total_commission: Decimal = Field(default=Decimal("0"))
    total_slippage: Decimal = Field(default=Decimal("0"))

    model_config = {
        "json_encoders": {Decimal: str, datetime: lambda v: v.isoformat()},
        "use_enum_values": True,
    }

    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    @property
    def result_id(self) -> str:
        """Get the unique backtest ID."""
        return self.metadata.backtest_id

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert result to dictionary for serialization.

        Returns:
            Dictionary representation suitable for JSON export
        """
        return {
            "metadata": self.metadata.model_dump(),
            "summary": {
                "total_return": str(self.total_return),
                "total_trades": self.total_trades,
                "win_rate": self.win_rate,
                "final_balance": str(self.final_balance),
                "sharpe_ratio": self.sharpe_ratio,
                "max_drawdown": self.max_drawdown,
            },
            "performance_metrics": {
                "sharpe_ratio": self.sharpe_ratio,
                "sortino_ratio": self.sortino_ratio,
                "max_drawdown": self.max_drawdown,
                "max_drawdown_date": self.max_drawdown_date.isoformat()
                if self.max_drawdown_date
                else None,
                "calmar_ratio": self.calmar_ratio,
                "volatility": self.volatility,
                "profit_factor": self.profit_factor,
                "expectancy": self.expectancy,
            },
            "portfolio_metrics": {
                "realized_pnl": str(self.realized_pnl),
                "unrealized_pnl": str(self.unrealized_pnl),
                "total_pnl": str(self.total_pnl),
                "net_exposure": str(self.net_exposure),
                "open_positions": self.open_positions,
                "closed_positions": self.closed_positions,
            },
            "trade_analytics": {
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": self.win_rate,
                "largest_win": str(self.largest_win),
                "largest_loss": str(self.largest_loss),
                "avg_win": str(self.avg_win) if self.avg_win else None,
                "avg_loss": str(self.avg_loss) if self.avg_loss else None,
                "avg_trade_duration_hours": self.avg_trade_duration_hours,
                "instruments_traded": self.instruments_traded,
            },
            "costs": {
                "total_commission": str(self.total_commission),
                "total_slippage": str(self.total_slippage),
            },
            "trades": self.trades,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnhancedBacktestResult":
        """
        Create EnhancedBacktestResult from dictionary.

        Args:
            data: Dictionary representation of result

        Returns:
            EnhancedBacktestResult instance
        """
        # Extract metadata
        metadata = BacktestMetadata(**data["metadata"])

        # Extract summary data
        summary = data.get("summary", {})
        performance = data.get("performance_metrics", {})
        portfolio = data.get("portfolio_metrics", {})
        trade_analytics = data.get("trade_analytics", {})
        costs = data.get("costs", {})

        return cls(
            metadata=metadata,
            # Basic metrics
            total_return=Decimal(summary.get("total_return", "0")),
            total_trades=trade_analytics.get("total_trades", 0),
            winning_trades=trade_analytics.get("winning_trades", 0),
            losing_trades=trade_analytics.get("losing_trades", 0),
            largest_win=Decimal(trade_analytics.get("largest_win", "0")),
            largest_loss=Decimal(trade_analytics.get("largest_loss", "0")),
            final_balance=Decimal(summary.get("final_balance", "0")),
            # Performance metrics
            sharpe_ratio=performance.get("sharpe_ratio"),
            sortino_ratio=performance.get("sortino_ratio"),
            max_drawdown=performance.get("max_drawdown"),
            max_drawdown_date=datetime.fromisoformat(performance["max_drawdown_date"])
            if performance.get("max_drawdown_date")
            else None,
            calmar_ratio=performance.get("calmar_ratio"),
            volatility=performance.get("volatility"),
            profit_factor=performance.get("profit_factor"),
            expectancy=performance.get("expectancy"),
            # Portfolio metrics
            realized_pnl=Decimal(portfolio.get("realized_pnl", "0")),
            unrealized_pnl=Decimal(portfolio.get("unrealized_pnl", "0")),
            total_pnl=Decimal(portfolio.get("total_pnl", "0")),
            net_exposure=Decimal(portfolio.get("net_exposure", "0")),
            open_positions=portfolio.get("open_positions", 0),
            closed_positions=portfolio.get("closed_positions", 0),
            # Trade analytics
            avg_win=Decimal(trade_analytics["avg_win"])
            if trade_analytics.get("avg_win")
            else None,
            avg_loss=Decimal(trade_analytics["avg_loss"])
            if trade_analytics.get("avg_loss")
            else None,
            avg_trade_duration_hours=trade_analytics.get("avg_trade_duration_hours"),
            instruments_traded=trade_analytics.get("instruments_traded", 0),
            # Costs
            total_commission=Decimal(costs.get("total_commission", "0")),
            total_slippage=Decimal(costs.get("total_slippage", "0")),
            # Trades
            trades=data.get("trades", []),
        )

    @classmethod
    def from_basic_result(
        cls,
        basic_result: Any,
        metadata: BacktestMetadata,
        performance_metrics: Optional[Dict[str, Any]] = None,
        portfolio_metrics: Optional[Dict[str, Any]] = None,
        trades: Optional[List[TradeModel]] = None,
    ) -> "EnhancedBacktestResult":
        """
        Create EnhancedBacktestResult from basic BacktestResult.

        Args:
            basic_result: Basic BacktestResult instance
            metadata: Backtest metadata
            performance_metrics: Optional performance metrics dictionary
            portfolio_metrics: Optional portfolio metrics dictionary
            trades: Optional list of TradeModel instances

        Returns:
            EnhancedBacktestResult instance
        """
        # Process trades
        trades_data = []
        if trades:
            for trade in trades:
                trades_data.append(trade.to_dict())

        # Extract performance metrics
        perf = performance_metrics or {}
        port = portfolio_metrics or {}

        return cls(
            metadata=metadata,
            # Basic results
            total_return=Decimal(str(basic_result.total_return)),
            total_trades=basic_result.total_trades,
            winning_trades=basic_result.winning_trades,
            losing_trades=basic_result.losing_trades,
            largest_win=Decimal(str(basic_result.largest_win)),
            largest_loss=Decimal(str(basic_result.largest_loss)),
            final_balance=Decimal(str(basic_result.final_balance)),
            # Performance metrics
            sharpe_ratio=perf.get("sharpe_ratio"),
            sortino_ratio=perf.get("sortino_ratio"),
            max_drawdown=perf.get("max_drawdown"),
            max_drawdown_date=perf.get("max_drawdown_date"),
            calmar_ratio=perf.get("calmar_ratio"),
            volatility=perf.get("volatility"),
            profit_factor=perf.get("profit_factor"),
            expectancy=perf.get("expectancy"),
            # Portfolio metrics
            realized_pnl=Decimal(str(port.get("realized_pnl", 0))),
            unrealized_pnl=Decimal(str(port.get("unrealized_pnl", 0))),
            total_pnl=Decimal(str(port.get("total_pnl", 0))),
            net_exposure=Decimal(str(port.get("net_exposure", 0))),
            open_positions=port.get("open_positions", 0),
            closed_positions=port.get("closed_positions", 0),
            # Trade analytics
            avg_win=Decimal(str(perf.get("avg_win", 0)))
            if perf.get("avg_win")
            else None,
            avg_loss=Decimal(str(perf.get("avg_loss", 0)))
            if perf.get("avg_loss")
            else None,
            avg_trade_duration_hours=port.get("avg_trade_duration_hours"),
            instruments_traded=port.get("instruments_traded", 0),
            # Trades
            trades=trades_data,
        )

    def get_summary_dict(self) -> Dict[str, Any]:
        """
        Get concise summary dictionary for display.

        Returns:
            Dictionary with key summary metrics
        """
        return {
            "backtest_id": self.result_id,
            "timestamp": self.metadata.timestamp,
            "strategy": self.metadata.strategy_name,
            "symbol": self.metadata.symbol,
            "period": f"{self.metadata.start_date.date()} to {self.metadata.end_date.date()}",
            "total_return": self.total_return,
            "total_trades": self.total_trades,
            "win_rate": f"{self.win_rate:.1f}%",
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "final_balance": self.final_balance,
        }

    def __str__(self) -> str:
        """String representation of result."""
        return (
            f"EnhancedBacktestResult(id={self.result_id[:8]}, "
            f"strategy={self.metadata.strategy_name}, "
            f"return={self.total_return}, "
            f"trades={self.total_trades}, "
            f"win_rate={self.win_rate:.1f}%)"
        )

    def __repr__(self) -> str:
        """Detailed representation of result."""
        return (
            f"EnhancedBacktestResult("
            f"id='{self.result_id}', "
            f"strategy='{self.metadata.strategy_name}', "
            f"symbol='{self.metadata.symbol}', "
            f"total_return={self.total_return}, "
            f"sharpe_ratio={self.sharpe_ratio})"
        )
