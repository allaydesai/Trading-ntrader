"""
Backtest results extraction utilities.

This module provides functions for extracting comprehensive results and metrics
from a Nautilus Trader backtest engine after execution.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import structlog
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId, Venue

from src.config import get_settings
from src.core.mark_to_market import Fill, RiskMetrics, daily_equity, risk_metrics
from src.models.backtest_result import BacktestResult

logger = structlog.get_logger(__name__)

_UNSET: Any = object()
#: Largest gap, as a fraction of capital, tolerated between the rebuilt equity of
#: a run that ended flat and the account balance the engine reports.
_EQUITY_TOLERANCE = 1e-6


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
        bars_by_instrument: dict[InstrumentId, list[Bar]] | None = None,
    ):
        """
        Initialize the results extractor.

        Args:
            engine: The backtest engine to extract results from
            venue: The venue used in the backtest (defaults to SIM)
            settings: Application settings (defaults to get_settings())
            starting_balance: Actual starting balance used in backtest
                              (defaults to settings.default_balance)
            bars_by_instrument: The run's bars. When given, drawdown, Sharpe,
                              Sortino, volatility and the equity curve are
                              marked to market at each bar close instead of
                              being read from realised position returns.
        """
        self.engine = engine
        self.venue = venue if venue else Venue("SIM")
        self.settings = settings if settings else get_settings()
        self._starting_balance = (
            starting_balance
            if starting_balance is not None
            else float(self.settings.default_balance)
        )
        self._bars = bars_by_instrument
        self._mark_to_market: MarkToMarket | None = _UNSET

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
        risk = _risk_metrics(mark_to_market_of(self), analyzer, stats_returns)
        profit_factor = _safe_float(stats_returns.get("Profit Factor"))
        risk_return_ratio = _safe_float(stats_returns.get("Risk Return Ratio"))
        avg_return = _safe_float(stats_returns.get("Average (Return)"))
        avg_win_return = _safe_float(stats_returns.get("Average Win (Return)"))
        avg_loss_return = _safe_float(stats_returns.get("Average Loss (Return)"))

        # Extract PnL-based metrics
        total_pnl = _safe_float(stats_pnls.get("PnL (total)"))
        total_pnl_percentage = _safe_float(stats_pnls.get("PnL% (total)"))
        expectancy = _safe_float(stats_pnls.get("Expectancy"))
        avg_win = _side_stat(stats_pnls, "Avg Winner", traded=largest_win > 0)
        avg_loss = _side_stat(stats_pnls, "Avg Loser", traded=largest_loss < 0)
        max_winner = _side_stat(stats_pnls, "Max Winner", traded=largest_win > 0)
        max_loser = _side_stat(stats_pnls, "Max Loser", traded=largest_loss < 0)
        min_winner = _side_stat(stats_pnls, "Min Winner", traded=largest_win > 0)
        min_loser = _side_stat(stats_pnls, "Min Loser", traded=largest_loss < 0)

        # Calculate custom metrics
        cagr = None
        if start_date and end_date:
            cagr = _calculate_cagr(starting_balance, final_balance, start_date, end_date)

        return BacktestResult(
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            largest_win=largest_win,
            largest_loss=largest_loss,
            final_balance=final_balance,
            sharpe_ratio=risk.sharpe_ratio,
            sortino_ratio=risk.sortino_ratio,
            volatility=risk.volatility,
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
            max_drawdown=risk.max_drawdown,
            cagr=cagr,
            calmar_ratio=_calculate_calmar_ratio(cagr, risk.max_drawdown),
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

        marked = mark_to_market_of(self)
        if marked is not None:
            return _curve_points(marked.equity)
        try:
            returns = self.engine.portfolio.analyzer.returns()
            if returns is not None and len(returns) > 0:
                return _curve_points((1 + returns).cumprod() * self._starting_balance)

            # Fallback: build from positions
            return self._build_equity_curve_from_positions(
                self._starting_balance, start_date, end_date
            )

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


@dataclass(frozen=True)
class MarkToMarket:
    """A run's daily mark-to-market equity and the risk metrics computed from it."""

    equity: pd.Series
    risk: RiskMetrics


def _fills(engine: BacktestEngine) -> list[Fill]:
    """Every fill of the run. Orders are read, not positions: they are never snapshotted away."""
    fills = []
    for order in engine.cache.orders():
        for event in order.events:
            if not isinstance(event, OrderFilled):
                continue
            if event.commission.currency != USD:
                raise ValueError(f"commission in {event.commission.currency}, not USD")
            sign = 1.0 if event.order_side == OrderSide.BUY else -1.0
            instrument = engine.cache.instrument(event.instrument_id)
            fills.append(
                Fill(
                    ts_ns=event.ts_event,
                    instrument_id=str(event.instrument_id),
                    signed_qty=sign * event.last_qty.as_double(),
                    price=event.last_px.as_double(),
                    commission=event.commission.as_double(),
                    multiplier=instrument.multiplier.as_double(),
                )
            )
    return fills


def _check_against_accounts(
    engine: BacktestEngine, fills: list[Fill], venues: set[Venue], equity: pd.Series, capital: float
) -> None:
    """A run that ended flat must rebuild to exactly the balance the engine reports."""
    net: dict[str, float] = {}
    for fill in fills:
        net[fill.instrument_id] = net.get(fill.instrument_id, 0.0) + fill.signed_qty
    if any(abs(quantity) > 1e-9 for quantity in net.values()):
        return  # an open position's gain is in the equity but not in the balance
    accounts = [engine.cache.account_for_venue(venue) for venue in venues]
    reported = sum(float(a.balance_total(USD).as_double()) for a in accounts if a is not None)
    if abs(float(equity.iloc[-1]) - reported) > max(0.01, capital * _EQUITY_TOLERANCE):
        raise ValueError(f"rebuilt equity {float(equity.iloc[-1]):.2f} != balance {reported:.2f}")


def mark_to_market(
    engine: BacktestEngine,
    bars_by_instrument: dict[InstrumentId, list[Bar]],
    starting_balance: float,
) -> MarkToMarket | None:
    """Daily mark-to-market equity of a finished run, or None when it cannot be rebuilt.

    ``starting_balance`` is per venue account, as the engine seeds it. Only
    USD-quoted, non-inverse instruments are supported; anything else, and any
    run whose rebuilt equity disagrees with the engine's balance, returns None
    so the caller falls back to realised-return metrics.
    """
    try:
        for instrument_id in bars_by_instrument:
            instrument = engine.cache.instrument(instrument_id)
            if instrument.quote_currency != USD or instrument.is_inverse:
                raise ValueError(f"{instrument_id} is not a USD-quoted linear instrument")
        venues = {instrument_id.venue for instrument_id in bars_by_instrument}
        capital = starting_balance * len(venues)
        fills = _fills(engine)
        closes = {
            str(instrument_id): [(bar.ts_event, bar.close.as_double()) for bar in bars]
            for instrument_id, bars in bars_by_instrument.items()
        }
        equity = daily_equity(capital, fills, closes)
        if equity.empty:
            return None
        _check_against_accounts(engine, fills, venues, equity, capital)
        return MarkToMarket(equity, risk_metrics(equity, capital))
    except Exception as e:
        logger.warning("Mark-to-market equity unavailable; using realised returns", reason=str(e))
        return None


def mark_to_market_of(extractor: ResultsExtractor) -> MarkToMarket | None:
    """The extractor's mark-to-market result, computed once; None without bars."""
    if extractor._bars is None or not extractor.engine:
        return None
    if extractor._mark_to_market is _UNSET:
        extractor._mark_to_market = mark_to_market(
            extractor.engine, extractor._bars, extractor._starting_balance
        )
    return extractor._mark_to_market


def metrics_basis(extractor: ResultsExtractor | None) -> str:
    """``"mark_to_market"`` or ``"realised"``: what the risk metrics were computed from."""
    marked = mark_to_market_of(extractor) if extractor is not None else None
    return "mark_to_market" if marked is not None else "realised"


def _risk_metrics(marked: MarkToMarket | None, analyzer, stats_returns: dict) -> RiskMetrics:
    """Mark-to-market risk metrics, else the analyzer's realised-return ones."""
    if marked is not None:
        return marked.risk
    return RiskMetrics(
        max_drawdown=_calculate_max_drawdown(analyzer),
        sharpe_ratio=_safe_float(stats_returns.get("Sharpe Ratio (252 days)")),
        sortino_ratio=_safe_float(stats_returns.get("Sortino Ratio (252 days)")),
        volatility=_safe_float(stats_returns.get("Returns Volatility (252 days)")),
    )


def _curve_points(equity: pd.Series) -> list[dict[str, int | float]]:
    """``[{"time": unix_seconds, "value": equity}, ...]`` for the chart."""
    points: list[dict[str, int | float]] = []
    for timestamp, value in equity.items():
        if hasattr(timestamp, "timestamp"):
            time_unix = int(timestamp.timestamp())
        elif isinstance(timestamp, int):
            time_unix = timestamp
        else:
            continue
        points.append({"time": time_unix, "value": round(float(value), 2)})
    return points


def _side_stat(stats: dict[str, Any], name: str, *, traded: bool) -> float | None:
    """A winners-only or losers-only statistic; None when that side never traded.

    Nautilus reports 0.0 for e.g. "Avg Loser" when there were no losers, which
    reads as a real average and wins any "smallest loss" comparison by default.
    """
    return _safe_float(stats.get(name)) if traded else None


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
