"""
Trade analytics service for equity curve generation and performance metrics.

This module provides functions for analyzing trade history and generating
performance visualizations including equity curves, drawdown metrics, and
trade statistics.
"""

from datetime import datetime, timezone
from decimal import Decimal

from src.models.trade import (
    EquityCurvePoint,
    EquityCurveResponse,
    Trade,
)


def generate_equity_curve(
    trades: list[Trade],
    initial_capital: Decimal,
) -> EquityCurveResponse:
    """
    Generate equity curve from trade history.

    The equity curve shows how account balance evolved over time based on
    cumulative trade profits and losses. Each point represents the balance
    after a trade exit, starting from initial capital.

    Args:
        trades: List of Trade objects (can include open and closed trades)
        initial_capital: Starting account balance in base currency

    Returns:
        EquityCurveResponse with time-series balance data, including:
        - points: List of (timestamp, balance, cumulative_return_pct) tuples
        - initial_capital: Starting balance
        - final_balance: Ending balance after all trades
        - total_return_pct: Overall return percentage

    Example:
        >>> trades = [
        ...     Trade(profit_loss=Decimal("100"), exit_timestamp=dt1),
        ...     Trade(profit_loss=Decimal("-50"), exit_timestamp=dt2),
        ... ]
        >>> curve = generate_equity_curve(trades, Decimal("10000"))
        >>> len(curve.points)
        3  # Initial point + 2 trades
        >>> curve.final_balance
        Decimal('10050.00')

    Notes:
        - Open trades (exit_timestamp=None) are excluded from equity curve
        - Trades are automatically sorted by exit_timestamp (chronological order)
        - First point is always at initial_capital with 0% return
        - Cumulative return is calculated as: ((balance - initial) / initial) * 100
    """
    # Start with initial capital
    balance = initial_capital

    # Create initial point
    # Use first trade's entry time if available, otherwise current time (UTC)
    initial_timestamp = trades[0].entry_timestamp if trades else datetime.now(timezone.utc)

    points = [
        EquityCurvePoint(
            timestamp=initial_timestamp,
            balance=balance,
            cumulative_return_pct=Decimal("0.00"),
            trade_number=0,
        )
    ]

    # Filter out open trades and sort by exit timestamp
    closed_trades = [
        t for t in trades if t.exit_timestamp is not None and t.profit_loss is not None
    ]

    sorted_trades = sorted(
        closed_trades,
        key=lambda t: t.exit_timestamp,
    )

    # Calculate cumulative balance after each trade
    for idx, trade in enumerate(sorted_trades, start=1):
        # Add trade profit/loss to balance
        balance += trade.profit_loss

        # Calculate cumulative return percentage (rounded to 4 decimal places)
        cum_return = ((balance - initial_capital) / initial_capital) * 100
        cum_return = cum_return.quantize(Decimal("0.0001"))

        points.append(
            EquityCurvePoint(
                timestamp=trade.exit_timestamp,
                balance=balance,
                cumulative_return_pct=cum_return,
                trade_number=idx,
            )
        )

    # Calculate final metrics
    final_balance = balance
    total_return = ((final_balance - initial_capital) / initial_capital) * 100
    total_return = total_return.quantize(Decimal("0.0001"))

    return EquityCurveResponse(
        points=points,
        initial_capital=initial_capital,
        final_balance=final_balance,
        total_return_pct=total_return,
    )
