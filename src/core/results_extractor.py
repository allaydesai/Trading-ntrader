"""
Backtest results extraction utilities.

This module provides functions for extracting comprehensive results and metrics
from a Nautilus Trader backtest engine after execution.
"""

from datetime import datetime

import structlog
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.identifiers import Venue

from src.config import get_settings
from src.models.backtest_result import BacktestResult

logger = structlog.get_logger(__name__)


class ResultsExtractor:
    """
    Extracts comprehensive results from a backtest engine.

    This class handles all results extraction logic including:
    - Basic metrics (returns, trades, win/loss)
    - Advanced metrics (Sharpe, Sortino, volatility)
    - Equity curve extraction
    - CAGR and Calmar ratio calculations

    Example:
        >>> extractor = ResultsExtractor(engine, venue)
        >>> result = extractor.extract_results(start_date, end_date)
    """

    def __init__(
        self,
        engine: BacktestEngine,
        venue: Venue | None = None,
        settings=None,
        starting_balance: float | None = None,
    ):
        """
        Initialize the results extractor.

        Args:
            engine: The backtest engine to extract results from
            venue: The venue used in the backtest (defaults to SIM)
            settings: Application settings (defaults to get_settings())
            starting_balance: Actual starting balance used in backtest
                              (defaults to settings.default_balance)
        """
        self.engine = engine
        self.venue = venue if venue else Venue("SIM")
        self.settings = settings if settings else get_settings()
        self._starting_balance = (
            starting_balance
            if starting_balance is not None
            else float(self.settings.default_balance)
        )

    def extract_results(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> BacktestResult:
        """
        Extract comprehensive results from the backtest engine.

        Args:
            start_date: Backtest start date (for CAGR calculation)
            end_date: Backtest end date (for CAGR calculation)

        Returns:
            BacktestResult with all available metrics
        """
        if not self.engine:
            return BacktestResult()

        account = self.engine.cache.account_for_venue(self.venue)
        if not account:
            return BacktestResult()

        # Calculate basic metrics
        starting_balance = self._starting_balance
        final_balance = float(account.balance_total(USD).as_double())
        total_return = (final_balance - starting_balance) / starting_balance

        # Get trade statistics
        closed_positions = self.engine.cache.positions_closed()
        total_trades = len(closed_positions)

        winning_trades = 0
        losing_trades = 0
        largest_win = 0.0
        largest_loss = 0.0

        for position in closed_positions:
            pnl = (
                position.realized_pnl.as_double()
                if hasattr(position, "realized_pnl") and position.realized_pnl
                else 0.0
            )
            if pnl > 0:
                winning_trades += 1
                largest_win = max(largest_win, pnl)
            else:
                losing_trades += 1
                if pnl < 0:
                    largest_loss = min(largest_loss, pnl)

        # Extract advanced metrics
        analyzer = self.engine.portfolio.analyzer

        try:
            stats_returns = analyzer.get_performance_stats_returns()
            stats_pnls = analyzer.get_performance_stats_pnls(currency=USD)
        except Exception as e:
            logger.warning(f"Could not extract advanced metrics: {e}")
            stats_returns = {}
            stats_pnls = {}

        # Extract return-based metrics
        sharpe_ratio = _safe_float(stats_returns.get("Sharpe Ratio (252 days)"))
        sortino_ratio = _safe_float(stats_returns.get("Sortino Ratio (252 days)"))
        volatility = _safe_float(stats_returns.get("Returns Volatility (252 days)"))
        profit_factor = _safe_float(stats_returns.get("Profit Factor"))
        risk_return_ratio = _safe_float(stats_returns.get("Risk Return Ratio"))
        avg_return = _safe_float(stats_returns.get("Average (Return)"))
        avg_win_return = _safe_float(stats_returns.get("Average Win (Return)"))
        avg_loss_return = _safe_float(stats_returns.get("Average Loss (Return)"))

        # Extract PnL-based metrics
        total_pnl = _safe_float(stats_pnls.get("PnL (total)"))
        total_pnl_percentage = _safe_float(stats_pnls.get("PnL% (total)"))
        expectancy = _safe_float(stats_pnls.get("Expectancy"))
        avg_win = _safe_float(stats_pnls.get("Avg Winner"))
        avg_loss = _safe_float(stats_pnls.get("Avg Loser"))
        max_winner = _safe_float(stats_pnls.get("Max Winner"))
        max_loser = _safe_float(stats_pnls.get("Max Loser"))
        min_winner = _safe_float(stats_pnls.get("Min Winner"))
        min_loser = _safe_float(stats_pnls.get("Min Loser"))

        # Calculate custom metrics
        max_drawdown = _calculate_max_drawdown(analyzer)
        cagr = None
        if start_date and end_date:
            cagr = _calculate_cagr(starting_balance, final_balance, start_date, end_date)
        calmar_ratio = _calculate_calmar_ratio(cagr, max_drawdown)

        return BacktestResult(
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            largest_win=largest_win,
            largest_loss=largest_loss,
            final_balance=final_balance,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            volatility=volatility,
            profit_factor=profit_factor,
            risk_return_ratio=risk_return_ratio,
            avg_return=avg_return,
            avg_win_return=avg_win_return,
            avg_loss_return=avg_loss_return,
            total_pnl=total_pnl,
            total_pnl_percentage=total_pnl_percentage,
            expectancy=expectancy,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_winner=max_winner,
            max_loser=max_loser,
            min_winner=min_winner,
            min_loser=min_loser,
            max_drawdown=max_drawdown,
            cagr=cagr,
            calmar_ratio=calmar_ratio,
        )

    def extract_equity_curve(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, int | float]]:
        """
        Extract equity curve for chart visualization.

        Args:
            start_date: Backtest start date (for curve boundaries)
            end_date: Backtest end date (for curve boundaries)

        Returns:
            List of equity points: [{"time": unix_ts, "value": equity}, ...]
        """
        if not self.engine:
            return []

        analyzer = self.engine.portfolio.analyzer
        starting_balance = self._starting_balance

        try:
            returns = analyzer.returns()
            if returns is not None and len(returns) > 0:
                cumulative_returns = (1 + returns).cumprod()
                equity_values = cumulative_returns * starting_balance

                equity_curve = []
                for timestamp, value in equity_values.items():
                    if hasattr(timestamp, "timestamp"):
                        time_unix = int(timestamp.timestamp())
                    elif isinstance(timestamp, int):
                        time_unix = timestamp
                    else:
                        continue
                    equity_curve.append({"time": time_unix, "value": round(float(value), 2)})

                return equity_curve

            # Fallback: build from positions
            return self._build_equity_curve_from_positions(starting_balance, start_date, end_date)

        except Exception as e:
            logger.warning(f"Could not extract equity curve: {e}")
            return []

    def _build_equity_curve_from_positions(
        self,
        starting_balance: float,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, int | float]]:
        """Build equity curve from closed positions."""
        if not self.engine:
            return []

        closed_positions = self.engine.cache.positions_closed()
        if not closed_positions:
            return []

        equity_points = []
        cumulative_pnl = 0.0

        if start_date:
            equity_points.append(
                {
                    "time": int(start_date.timestamp()),
                    "value": round(starting_balance, 2),
                }
            )

        sorted_positions = sorted(
            [p for p in closed_positions if hasattr(p, "ts_closed") and p.ts_closed],
            key=lambda x: x.ts_closed,
        )

        for position in sorted_positions:
            pnl = (
                position.realized_pnl.as_double()
                if hasattr(position, "realized_pnl") and position.realized_pnl
                else 0.0
            )
            cumulative_pnl += pnl
            equity_value = starting_balance + cumulative_pnl
            time_unix = int(position.ts_closed / 1_000_000_000)
            equity_points.append({"time": time_unix, "value": round(equity_value, 2)})

        if end_date and equity_points:
            equity_points.append(
                {
                    "time": int(end_date.timestamp()),
                    "value": equity_points[-1]["value"],
                }
            )

        return equity_points


def _safe_float(value) -> float | None:
    """Safely convert a value to float, handling NaN and infinity."""
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return None
    try:
        result = float(value)
        if result != result or abs(result) == float("inf"):
            return None
        return result
    except (ValueError, TypeError):
        return None


def _calculate_max_drawdown(analyzer) -> float | None:
    """Calculate maximum drawdown from returns data."""
    try:
        returns = analyzer.returns()
        if returns is None or len(returns) == 0:
            return None

        cumulative_returns = (1 + returns).cumprod()
        running_max = cumulative_returns.expanding().max()
        drawdowns = (cumulative_returns - running_max) / running_max
        max_drawdown = float(drawdowns.min())
        return max_drawdown if max_drawdown < 0 else 0.0
    except Exception as e:
        logger.warning(f"Could not calculate max drawdown: {e}")
        return None


def _calculate_cagr(
    starting_balance: float,
    final_balance: float,
    start_date: datetime,
    end_date: datetime,
) -> float | None:
    """Calculate Compound Annual Growth Rate."""
    try:
        if starting_balance <= 0 or final_balance <= 0:
            return None

        days = (end_date - start_date).days
        if days <= 0:
            return None

        years = days / 365.25
        return float((final_balance / starting_balance) ** (1 / years) - 1)
    except Exception as e:
        logger.warning(f"Could not calculate CAGR: {e}")
        return None


def _calculate_calmar_ratio(
    cagr: float | None,
    max_drawdown: float | None,
) -> float | None:
    """Calculate Calmar Ratio (CAGR / |max drawdown|)."""
    if cagr is None or max_drawdown is None or max_drawdown == 0:
        return None
    return float(cagr / abs(max_drawdown))
