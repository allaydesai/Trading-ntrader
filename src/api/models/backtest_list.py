"""
Backtest list view models for web UI rendering.

Provides data structures for paginated backtest table display.
"""

from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from src.api.models.filter_models import FilterState
from src.db.models.backtest import BacktestRun


class BacktestListItem(BaseModel):
    """
    Row data for paginated backtest table.

    Attributes:
        run_id: Business identifier (for navigation)
        run_id_short: First 8 chars for display
        strategy_name: Strategy name (max 50 chars display)
        instrument_symbol: Trading symbol
        date_range: "YYYY-MM-DD to YYYY-MM-DD"
        total_return: Return as decimal ratio (e.g., 0.9519 for 95.19%)
        final_balance: Final portfolio balance (None if failed)
        sharpe_ratio: Risk-adjusted return (None if failed)
        max_drawdown: Peak-to-trough decline (None if failed)
        execution_status: Status indicator
        created_at: Execution timestamp

    Example:
        >>> item = BacktestListItem(
        ...     run_id=UUID("12345678-1234-5678-1234-567812345678"),
        ...     strategy_name="SMA Crossover Strategy",
        ...     instrument_symbol="AAPL",
        ...     date_range="2024-01-01 to 2024-12-31",
        ...     total_return=Decimal("0.9519"),
        ...     final_balance=Decimal("1004111.68"),
        ...     sharpe_ratio=Decimal("1.85"),
        ...     max_drawdown=Decimal("-0.15"),
        ...     execution_status="success",
        ...     created_at=datetime.now()
        ... )
        >>> print(item.return_percentage)  # 95.19%
    """

    run_id: UUID = Field(..., description="Business identifier")
    strategy_name: str = Field(..., description="Strategy name (max 50 chars display)")
    instrument_symbol: str = Field(..., description="Trading symbol")
    date_range: str = Field(..., description="YYYY-MM-DD to YYYY-MM-DD")
    total_return: Optional[Decimal] = Field(
        None, description="Return as decimal ratio, e.g., 0.9519 for 95.19% (None if failed)"
    )
    final_balance: Optional[Decimal] = Field(
        None, description="Final portfolio balance (None if failed)"
    )
    sharpe_ratio: Optional[Decimal] = Field(
        None, description="Risk-adjusted return (None if failed)"
    )
    max_drawdown: Optional[Decimal] = Field(
        None, description="Peak-to-trough decline (None if failed)"
    )
    execution_status: str = Field(..., description="success or failed")
    created_at: datetime = Field(..., description="Execution timestamp")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def run_id_short(self) -> str:
        """First 8 characters of UUID for display."""
        return str(self.run_id)[:8]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def return_percentage(self) -> float:
        """
        Convert return decimal ratio to percentage.

        Returns:
            Percentage return (e.g., 95.19 for 95.19% return).
            Returns 0.0 if total_return is None.

        Example:
            If total_return=0.9519 (stored as decimal ratio),
            return_percentage = 0.9519 * 100 = 95.19%
        """
        if self.total_return is None:
            return 0.0

        # total_return is already a decimal ratio (e.g., 0.9519 for 95.19%)
        # Simply multiply by 100 to get the percentage
        return float(self.total_return * 100)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_positive_return(self) -> bool:
        """Whether the total return is positive."""
        if self.total_return is None:
            return False
        return self.total_return > 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_color(self) -> str:
        """Color indicator for status."""
        return "green" if self.execution_status == "success" else "red"


class BacktestListPage(BaseModel):
    """
    Paginated response for backtest list endpoint.

    Attributes:
        backtests: Page of backtest items
        page: Current page number (1-indexed)
        page_size: Results per page
        total_count: Total backtests in system
        total_pages: Calculated pages
        has_next: More pages available
        has_previous: Previous page exists

    Example:
        >>> page = BacktestListPage(
        ...     backtests=[...],
        ...     page=1,
        ...     page_size=20,
        ...     total_count=42
        ... )
        >>> print(page.total_pages)  # 3
        >>> print(page.has_next)  # True
    """

    backtests: list[BacktestListItem] = Field(default_factory=list, description="Page of backtests")
    page: int = Field(1, ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(20, description="Results per page")
    total_count: int = Field(0, ge=0, description="Total backtests in system")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int:
        """Total number of pages."""
        if self.total_count == 0:
            return 1
        return max(1, ceil(self.total_count / self.page_size))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_next(self) -> bool:
        """Whether there is a next page."""
        return self.page < self.total_pages

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_previous(self) -> bool:
        """Whether there is a previous page."""
        return self.page > 1


class FilteredBacktestListPage(BacktestListPage):
    """
    Paginated response with filter state context.

    Extends BacktestListPage with filter state and available options
    for populating filter dropdowns.

    Attributes:
        filter_state: Current filter and sort state
        available_strategies: Distinct strategy names for dropdown
        available_instruments: Distinct instrument symbols for autocomplete

    Example:
        >>> from src.api.models.filter_models import FilterState
        >>> page = FilteredBacktestListPage(
        ...     backtests=[...],
        ...     page=1,
        ...     page_size=20,
        ...     total_count=42,
        ...     filter_state=FilterState(strategy="SMA Crossover"),
        ...     available_strategies=["SMA Crossover", "Mean Reversion"],
        ...     available_instruments=["AAPL", "SPY", "TSLA"]
        ... )
    """

    filter_state: FilterState = Field(..., description="Current filter and sort state")
    available_strategies: list[str] = Field(
        default_factory=list, description="Distinct strategy names"
    )
    available_instruments: list[str] = Field(
        default_factory=list, description="Distinct instrument symbols"
    )


def truncate_strategy_name(name: str, max_length: int = 50) -> str:
    """
    Truncate strategy name with ellipsis if too long.

    Args:
        name: Strategy name to truncate
        max_length: Maximum length (default 50)

    Returns:
        Truncated name with "..." if longer than max_length

    Example:
        >>> truncate_strategy_name("Very Long Strategy Name That Exceeds Limit", 20)
        "Very Long Strategy..."
    """
    if len(name) <= max_length:
        return name
    return name[: max_length - 3] + "..."


def to_list_item(run: BacktestRun) -> BacktestListItem:
    """
    Map BacktestRun database model to BacktestListItem view model.

    Args:
        run: BacktestRun database instance with metrics loaded

    Returns:
        BacktestListItem for table display

    Example:
        >>> # From database
        >>> run = await repository.find_recent(limit=1)[0]
        >>> # To view model
        >>> item = to_list_item(run)
        >>> print(f"{item.strategy_name}: {item.run_id_short}")
    """
    # Format date range
    date_range = f"{run.start_date.date()} to {run.end_date.date()}"

    # Extract metrics if available
    total_return = None
    final_balance = None
    sharpe_ratio = None
    max_drawdown = None

    if run.metrics and run.execution_status == "success":
        total_return = run.metrics.total_return
        final_balance = run.metrics.final_balance
        sharpe_ratio = run.metrics.sharpe_ratio
        max_drawdown = run.metrics.max_drawdown

    return BacktestListItem(
        run_id=run.run_id,
        strategy_name=truncate_strategy_name(run.strategy_name),
        instrument_symbol=run.instrument_symbol,
        date_range=date_range,
        total_return=total_return,
        final_balance=final_balance,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        execution_status=run.execution_status,
        created_at=run.created_at,
    )
