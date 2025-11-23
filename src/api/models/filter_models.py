"""
Filter and sort state models for interactive backtest lists.

Provides data structures for filtering, sorting, and pagination state management.
"""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class SortOrder(str, Enum):
    """Sort direction for backtest list."""

    ASC = "asc"
    DESC = "desc"


class ExecutionStatus(str, Enum):
    """Backtest execution outcome."""

    SUCCESS = "success"
    FAILED = "failed"


class SortColumn(str, Enum):
    """Valid columns for sorting backtest list."""

    CREATED_AT = "created_at"
    STRATEGY_NAME = "strategy_name"
    INSTRUMENT_SYMBOL = "instrument_symbol"
    TOTAL_RETURN = "total_return"
    SHARPE_RATIO = "sharpe_ratio"
    MAX_DRAWDOWN = "max_drawdown"
    EXECUTION_STATUS = "execution_status"


class FilterState(BaseModel):
    """
    Complete filter, sort, and pagination state for backtest list.

    Attributes:
        strategy: Filter by exact strategy name
        instrument: Filter by partial instrument symbol match
        date_from: Filter by minimum creation date
        date_to: Filter by maximum creation date
        status: Filter by execution status
        sort: Column to sort by
        order: Sort direction (asc/desc)
        page: Current page number (1-indexed)
        page_size: Number of results per page

    Example:
        >>> state = FilterState(
        ...     strategy="SMA Crossover",
        ...     sort=SortColumn.SHARPE_RATIO,
        ...     order=SortOrder.DESC,
        ...     page=1
        ... )
        >>> state.to_query_params()
        {'strategy': 'SMA Crossover', 'sort': 'sharpe_ratio', ...}
    """

    strategy: Optional[str] = Field(
        default=None, max_length=255, description="Filter by strategy name"
    )
    instrument: Optional[str] = Field(
        default=None, max_length=50, description="Filter by instrument symbol"
    )
    date_from: Optional[date] = Field(
        default=None, description="Filter backtests created on or after"
    )
    date_to: Optional[date] = Field(
        default=None, description="Filter backtests created on or before"
    )
    status: Optional[ExecutionStatus] = Field(
        default=None, description="Filter by execution status"
    )
    sort: SortColumn = Field(default=SortColumn.CREATED_AT, description="Column to sort by")
    order: SortOrder = Field(default=SortOrder.DESC, description="Sort direction")
    page: int = Field(default=1, ge=1, description="Current page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Results per page")

    @model_validator(mode="after")
    def validate_date_range(self) -> "FilterState":
        """Ensure end date is not before start date."""
        if self.date_from and self.date_to:
            if self.date_to < self.date_from:
                raise ValueError("End date must be on or after start date")
        return self

    def to_query_params(self) -> dict[str, str]:
        """
        Convert filter state to URL query parameters.

        Returns:
            Dictionary of non-None parameters for URL construction.

        Example:
            >>> state = FilterState(strategy="SMA", page=2)
            >>> state.to_query_params()
            {'strategy': 'SMA', 'sort': 'created_at', 'order': 'desc', 'page': '2'}
        """
        params: dict[str, str] = {}
        if self.strategy:
            params["strategy"] = self.strategy
        if self.instrument:
            params["instrument"] = self.instrument
        if self.date_from:
            params["date_from"] = self.date_from.isoformat()
        if self.date_to:
            params["date_to"] = self.date_to.isoformat()
        if self.status:
            params["status"] = self.status.value
        params["sort"] = self.sort.value
        params["order"] = self.order.value
        params["page"] = str(self.page)
        params["page_size"] = str(self.page_size)
        return params

    def with_page(self, page: int) -> "FilterState":
        """
        Return new FilterState with updated page number.

        Args:
            page: New page number

        Returns:
            New FilterState instance with updated page
        """
        return self.model_copy(update={"page": page})

    def with_sort(self, column: SortColumn) -> "FilterState":
        """
        Return new FilterState with updated sort column.

        If same column clicked, toggles order. Otherwise, sorts desc.

        Args:
            column: Column to sort by

        Returns:
            New FilterState with updated sort and reset to page 1
        """
        if self.sort == column:
            new_order = SortOrder.ASC if self.order == SortOrder.DESC else SortOrder.DESC
        else:
            new_order = SortOrder.DESC
        return self.model_copy(update={"sort": column, "order": new_order, "page": 1})

    def clear_filters(self) -> "FilterState":
        """
        Return new FilterState with all filters cleared but sort preserved.

        Returns:
            New FilterState with filters reset to None, sort preserved
        """
        return FilterState(
            strategy=None,
            instrument=None,
            date_from=None,
            date_to=None,
            status=None,
            sort=self.sort,
            order=self.order,
            page=1,
            page_size=self.page_size,
        )


class SortableColumn(BaseModel):
    """
    Column header metadata for sortable table.

    Used by Jinja template to render clickable headers with indicators.

    Attributes:
        name: Column identifier
        label: Display label for header
        css_class: CSS classes for alignment
        is_current: Whether this column is currently sorted
        sort_indicator: Arrow icon or empty
        next_sort_url: URL for clicking this header
    """

    name: SortColumn
    label: str
    css_class: str = "text-left"
    is_current: bool = False
    sort_indicator: str = ""
    next_sort_url: str = ""


class PaginationControl(BaseModel):
    """
    Pagination button metadata.

    Used by Jinja template to render pagination controls.

    Attributes:
        page_number: Page number
        url: URL for this page
        is_current: Whether this is current page
        is_disabled: Whether button should be disabled
        label: Display text
    """

    page_number: int
    url: str
    is_current: bool = False
    is_disabled: bool = False
    label: str = ""
