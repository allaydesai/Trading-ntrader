"""
Trade analytics service for equity curve generation and performance metrics.

This module provides functions for analyzing trade history and generating
performance visualizations including equity curves, drawdown metrics, and
trade statistics.
"""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from src.models.trade import (
    EquityCurvePoint,
    EquityCurveResponse,
    Trade,
    TradeStatistics,
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
        cum_return = cum_return.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

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
    total_return = total_return.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    return EquityCurveResponse(
        points=points,
        initial_capital=initial_capital,
        final_balance=final_balance,
        total_return_pct=total_return,
    )


def calculate_trade_statistics(trades: list[Trade]) -> TradeStatistics:
    """
    Calculate comprehensive trade performance statistics.

    Analyzes trade history to compute win rate, profit metrics, risk metrics,
    consecutive streaks, and holding period statistics. Only closed trades
    (with exit_timestamp and profit_loss) are included in calculations.

    Args:
        trades: List of Trade objects (open trades are excluded automatically)

    Returns:
        TradeStatistics object with comprehensive performance metrics

    Example:
        >>> trades = [
        ...     Trade(profit_loss=Decimal("100"), ...),
        ...     Trade(profit_loss=Decimal("-50"), ...),
        ... ]
        >>> stats = calculate_trade_statistics(trades)
        >>> stats.win_rate
        Decimal('50.00')
        >>> stats.profit_factor
        Decimal('2.00')

    Notes:
        - Breakeven trades (profit_loss <= 0 but > -10) are counted separately
        - Win rate = (winning_trades / total_trades) * 100
        - Profit factor = total_profit / abs(total_loss) (None if no losses)
        - Expectancy = net_profit / total_trades (average profit per trade)
        - Holding periods are calculated in hours from seconds
    """
    # Filter only closed trades with profit_loss data
    closed_trades = [
        t for t in trades if t.exit_timestamp is not None and t.profit_loss is not None
    ]

    # Handle empty trades case
    if not closed_trades:
        return TradeStatistics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            win_rate=Decimal("0.00"),
            total_profit=Decimal("0.00"),
            total_loss=Decimal("0.00"),
            net_profit=Decimal("0.00"),
            average_win=Decimal("0.00"),
            average_loss=Decimal("0.00"),
            largest_win=Decimal("0.00"),
            largest_loss=Decimal("0.00"),
            profit_factor=None,
            expectancy=Decimal("0.00"),
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            avg_holding_period_hours=Decimal("0.00"),
            max_holding_period_hours=0,
            min_holding_period_hours=0,
        )

    # Categorize trades
    winning_trades = []
    losing_trades = []
    breakeven_trades = []

    for trade in closed_trades:
        # Breakeven threshold: profit/loss between -10 and 0 (basically just commission)
        if trade.profit_loss > Decimal("0"):
            winning_trades.append(trade)
        elif trade.profit_loss > Decimal("-10") and trade.profit_loss <= Decimal("0"):
            breakeven_trades.append(trade)
        else:
            losing_trades.append(trade)

    # Calculate trade counts
    total_trades = len(closed_trades)
    num_wins = len(winning_trades)
    num_losses = len(losing_trades)
    num_breakeven = len(breakeven_trades)

    # Calculate win rate (breakeven and losses count as non-wins)
    win_rate = (
        (Decimal(num_wins) / Decimal(total_trades)) * 100 if total_trades > 0 else Decimal("0.00")
    )
    win_rate = win_rate.quantize(Decimal("0.01"))

    # Calculate profit metrics
    total_profit = sum((t.profit_loss for t in winning_trades), Decimal("0"))
    total_loss = abs(sum((t.profit_loss for t in losing_trades), Decimal("0")))
    net_profit = total_profit - total_loss

    average_win = total_profit / num_wins if num_wins > 0 else Decimal("0.00")
    average_loss = total_loss / num_losses if num_losses > 0 else Decimal("0.00")

    largest_win = max((t.profit_loss for t in winning_trades), default=Decimal("0.00"))
    largest_loss = abs(min((t.profit_loss for t in losing_trades), default=Decimal("0.00")))

    # Calculate profit factor
    profit_factor = None
    if total_loss > 0:
        profit_factor = (total_profit / total_loss).quantize(Decimal("0.01"))

    # Calculate expectancy (average profit per trade)
    expectancy = net_profit / total_trades if total_trades > 0 else Decimal("0.00")
    expectancy = expectancy.quantize(Decimal("0.01"))

    # Calculate consecutive streaks
    max_consecutive_wins = _calculate_max_consecutive_wins(closed_trades)
    max_consecutive_losses = _calculate_max_consecutive_losses(closed_trades)

    # Calculate holding period statistics
    holding_periods = [
        t.holding_period_seconds for t in closed_trades if t.holding_period_seconds is not None
    ]

    if holding_periods:
        # Convert seconds to hours
        holding_periods_hours = [Decimal(str(sec / 3600)) for sec in holding_periods]
        avg_holding_hours = sum(holding_periods_hours) / len(holding_periods_hours)
        avg_holding_hours = avg_holding_hours.quantize(Decimal("0.01"))
        max_holding_hours = int(max(holding_periods) / 3600)
        min_holding_hours = int(min(holding_periods) / 3600)
    else:
        avg_holding_hours = Decimal("0.00")
        max_holding_hours = 0
        min_holding_hours = 0

    return TradeStatistics(
        total_trades=total_trades,
        winning_trades=num_wins,
        losing_trades=num_losses,
        breakeven_trades=num_breakeven,
        win_rate=win_rate,
        total_profit=total_profit,
        total_loss=total_loss,
        net_profit=net_profit,
        average_win=average_win,
        average_loss=average_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_consecutive_wins=max_consecutive_wins,
        max_consecutive_losses=max_consecutive_losses,
        avg_holding_period_hours=avg_holding_hours,
        max_holding_period_hours=max_holding_hours,
        min_holding_period_hours=min_holding_hours,
    )


def _calculate_max_consecutive_wins(trades: list[Trade]) -> int:
    """
    Calculate the maximum consecutive winning streak.

    Args:
        trades: List of closed trades (should already be filtered)

    Returns:
        Maximum number of consecutive winning trades
    """
    if not trades:
        return 0

    # Sort trades by exit timestamp to get chronological order
    sorted_trades = sorted(trades, key=lambda t: t.exit_timestamp)

    max_streak = 0
    current_streak = 0

    for trade in sorted_trades:
        if trade.profit_loss and trade.profit_loss > Decimal("0"):
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return max_streak


def _calculate_max_consecutive_losses(trades: list[Trade]) -> int:
    """
    Calculate the maximum consecutive losing streak.

    Args:
        trades: List of closed trades (should already be filtered)

    Returns:
        Maximum number of consecutive losing trades
    """
    if not trades:
        return 0

    # Sort trades by exit timestamp to get chronological order
    sorted_trades = sorted(trades, key=lambda t: t.exit_timestamp)

    max_streak = 0
    current_streak = 0

    for trade in sorted_trades:
        if trade.profit_loss and trade.profit_loss <= Decimal("0"):
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return max_streak
