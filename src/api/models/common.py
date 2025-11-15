"""
Common view models for web UI rendering.

Provides shared data structures used across multiple pages.
"""

from typing import Optional

from pydantic import BaseModel, Field


class EmptyStateMessage(BaseModel):
    """
    User-friendly message when no data is available.

    Provides helpful context and suggested actions when pages have no data
    to display, improving user experience for empty states.

    Attributes:
        title: Short message title
        description: Helpful explanation
        action_text: Suggested action text (optional)
        action_command: CLI command example (optional)

    Example:
        >>> msg = EmptyStateMessage(
        ...     title="No Backtests Yet",
        ...     description="You haven't run any backtests yet.",
        ...     action_text="Run your first backtest",
        ...     action_command="ntrader backtest run --strategy sma_crossover --symbol AAPL"
        ... )
    """

    title: str = Field(
        ..., min_length=1, max_length=100, description="Short message title"
    )
    description: str = Field(
        ..., min_length=1, max_length=500, description="Helpful explanation"
    )
    action_text: Optional[str] = Field(
        None, max_length=100, description="Suggested action text"
    )
    action_command: Optional[str] = Field(
        None, max_length=200, description="CLI command example"
    )
