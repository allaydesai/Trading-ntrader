"""
Unit tests for FilterState and related filter models.

Tests validation logic, state transformations, and serialization.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from src.api.models.filter_models import (
    ExecutionStatus,
    FilterState,
    PaginationControl,
    SortableColumn,
    SortColumn,
    SortOrder,
)


class TestFilterStateValidation:
    """Test FilterState validation rules (T008)."""

    def test_valid_date_range_passes(self) -> None:
        """Date range where end >= start is valid."""
        state = FilterState(
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
        )
        assert state.date_from == date(2024, 1, 1)
        assert state.date_to == date(2024, 12, 31)

    def test_same_date_range_is_valid(self) -> None:
        """Same start and end date is valid."""
        state = FilterState(
            date_from=date(2024, 6, 15),
            date_to=date(2024, 6, 15),
        )
        assert state.date_from == state.date_to

    def test_invalid_date_range_raises_error(self) -> None:
        """Date range where end < start raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            FilterState(
                date_from=date(2024, 12, 31),
                date_to=date(2024, 1, 1),
            )
        assert "End date must be on or after start date" in str(exc_info.value)

    def test_page_must_be_positive(self) -> None:
        """Page number must be >= 1."""
        with pytest.raises(ValidationError):
            FilterState(page=0)

        with pytest.raises(ValidationError):
            FilterState(page=-1)

    def test_page_size_bounds(self) -> None:
        """Page size must be between 1 and 100."""
        # Valid bounds
        state = FilterState(page_size=1)
        assert state.page_size == 1

        state = FilterState(page_size=100)
        assert state.page_size == 100

        # Out of bounds
        with pytest.raises(ValidationError):
            FilterState(page_size=0)

        with pytest.raises(ValidationError):
            FilterState(page_size=101)

    def test_strategy_max_length(self) -> None:
        """Strategy name must be <= 255 characters."""
        long_name = "A" * 255
        state = FilterState(strategy=long_name)
        assert state.strategy is not None
        assert len(state.strategy) == 255

        with pytest.raises(ValidationError):
            FilterState(strategy="A" * 256)

    def test_instrument_max_length(self) -> None:
        """Instrument symbol must be <= 50 characters."""
        long_symbol = "X" * 50
        state = FilterState(instrument=long_symbol)
        assert state.instrument is not None
        assert len(state.instrument) == 50

        with pytest.raises(ValidationError):
            FilterState(instrument="X" * 51)

    def test_default_values(self) -> None:
        """Default values are set correctly."""
        state = FilterState()
        assert state.strategy is None
        assert state.instrument is None
        assert state.date_from is None
        assert state.date_to is None
        assert state.status is None
        assert state.sort == SortColumn.CREATED_AT
        assert state.order == SortOrder.DESC
        assert state.page == 1
        assert state.page_size == 20

    def test_valid_status_values(self) -> None:
        """ExecutionStatus enum values are valid."""
        state = FilterState(status=ExecutionStatus.SUCCESS)
        assert state.status == ExecutionStatus.SUCCESS

        state = FilterState(status=ExecutionStatus.FAILED)
        assert state.status == ExecutionStatus.FAILED

    def test_valid_sort_columns(self) -> None:
        """All SortColumn enum values are valid."""
        for column in SortColumn:
            state = FilterState(sort=column)
            assert state.sort == column


class TestFilterStateToQueryParams:
    """Test FilterState.to_query_params() method (T009)."""

    def test_default_state_params(self) -> None:
        """Default state includes sort, order, page, page_size."""
        state = FilterState()
        params = state.to_query_params()

        assert params["sort"] == "created_at"
        assert params["order"] == "desc"
        assert params["page"] == "1"
        assert params["page_size"] == "20"
        assert "strategy" not in params
        assert "instrument" not in params

    def test_strategy_included_when_set(self) -> None:
        """Strategy filter is included in params."""
        state = FilterState(strategy="SMA Crossover")
        params = state.to_query_params()

        assert params["strategy"] == "SMA Crossover"

    def test_instrument_included_when_set(self) -> None:
        """Instrument filter is included in params."""
        state = FilterState(instrument="AAPL")
        params = state.to_query_params()

        assert params["instrument"] == "AAPL"

    def test_date_range_included_when_set(self) -> None:
        """Date range filters are included as ISO format."""
        state = FilterState(
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
        )
        params = state.to_query_params()

        assert params["date_from"] == "2024-01-01"
        assert params["date_to"] == "2024-12-31"

    def test_status_included_when_set(self) -> None:
        """Status filter is included as enum value."""
        state = FilterState(status=ExecutionStatus.SUCCESS)
        params = state.to_query_params()

        assert params["status"] == "success"

    def test_all_params_combined(self) -> None:
        """All parameters are included correctly."""
        state = FilterState(
            strategy="Mean Reversion",
            instrument="SPY",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 30),
            status=ExecutionStatus.FAILED,
            sort=SortColumn.SHARPE_RATIO,
            order=SortOrder.ASC,
            page=3,
            page_size=50,
        )
        params = state.to_query_params()

        assert params == {
            "strategy": "Mean Reversion",
            "instrument": "SPY",
            "date_from": "2024-01-01",
            "date_to": "2024-06-30",
            "status": "failed",
            "sort": "sharpe_ratio",
            "order": "asc",
            "page": "3",
            "page_size": "50",
        }


class TestFilterStateWithSort:
    """Test FilterState.with_sort() toggle behavior (T010)."""

    def test_sort_different_column_sets_descending(self) -> None:
        """Sorting by different column defaults to descending."""
        state = FilterState(sort=SortColumn.CREATED_AT, order=SortOrder.DESC)
        new_state = state.with_sort(SortColumn.SHARPE_RATIO)

        assert new_state.sort == SortColumn.SHARPE_RATIO
        assert new_state.order == SortOrder.DESC

    def test_sort_same_column_toggles_ascending(self) -> None:
        """Clicking same column toggles DESC -> ASC."""
        state = FilterState(sort=SortColumn.SHARPE_RATIO, order=SortOrder.DESC)
        new_state = state.with_sort(SortColumn.SHARPE_RATIO)

        assert new_state.sort == SortColumn.SHARPE_RATIO
        assert new_state.order == SortOrder.ASC

    def test_sort_same_column_toggles_descending(self) -> None:
        """Clicking same column toggles ASC -> DESC."""
        state = FilterState(sort=SortColumn.SHARPE_RATIO, order=SortOrder.ASC)
        new_state = state.with_sort(SortColumn.SHARPE_RATIO)

        assert new_state.sort == SortColumn.SHARPE_RATIO
        assert new_state.order == SortOrder.DESC

    def test_sort_resets_page_to_one(self) -> None:
        """Sorting resets pagination to page 1."""
        state = FilterState(page=5)
        new_state = state.with_sort(SortColumn.TOTAL_RETURN)

        assert new_state.page == 1

    def test_sort_preserves_filters(self) -> None:
        """Sorting preserves existing filters."""
        state = FilterState(
            strategy="SMA Crossover",
            instrument="AAPL",
            status=ExecutionStatus.SUCCESS,
        )
        new_state = state.with_sort(SortColumn.MAX_DRAWDOWN)

        assert new_state.strategy == "SMA Crossover"
        assert new_state.instrument == "AAPL"
        assert new_state.status == ExecutionStatus.SUCCESS

    def test_sort_returns_new_instance(self) -> None:
        """with_sort returns a new instance, not mutating original."""
        state = FilterState(sort=SortColumn.CREATED_AT)
        new_state = state.with_sort(SortColumn.SHARPE_RATIO)

        assert state is not new_state
        assert state.sort == SortColumn.CREATED_AT  # Original unchanged


class TestFilterStateWithPage:
    """Test FilterState.with_page() method (T011)."""

    def test_with_page_updates_page_number(self) -> None:
        """with_page updates the page number."""
        state = FilterState(page=1)
        new_state = state.with_page(3)

        assert new_state.page == 3

    def test_with_page_preserves_filters(self) -> None:
        """with_page preserves all filters and sort."""
        state = FilterState(
            strategy="SMA Crossover",
            sort=SortColumn.SHARPE_RATIO,
            order=SortOrder.ASC,
        )
        new_state = state.with_page(5)

        assert new_state.strategy == "SMA Crossover"
        assert new_state.sort == SortColumn.SHARPE_RATIO
        assert new_state.order == SortOrder.ASC

    def test_with_page_returns_new_instance(self) -> None:
        """with_page returns a new instance."""
        state = FilterState(page=1)
        new_state = state.with_page(2)

        assert state is not new_state
        assert state.page == 1  # Original unchanged


class TestFilterStateClearFilters:
    """Test FilterState.clear_filters() method (T012)."""

    def test_clear_filters_resets_all_filters(self) -> None:
        """clear_filters removes all filter values."""
        state = FilterState(
            strategy="SMA Crossover",
            instrument="AAPL",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            status=ExecutionStatus.SUCCESS,
        )
        cleared = state.clear_filters()

        assert cleared.strategy is None
        assert cleared.instrument is None
        assert cleared.date_from is None
        assert cleared.date_to is None
        assert cleared.status is None

    def test_clear_filters_preserves_sort(self) -> None:
        """clear_filters preserves sort column and order."""
        state = FilterState(
            strategy="SMA",
            sort=SortColumn.SHARPE_RATIO,
            order=SortOrder.ASC,
        )
        cleared = state.clear_filters()

        assert cleared.sort == SortColumn.SHARPE_RATIO
        assert cleared.order == SortOrder.ASC

    def test_clear_filters_resets_page_to_one(self) -> None:
        """clear_filters resets page to 1."""
        state = FilterState(page=5)
        cleared = state.clear_filters()

        assert cleared.page == 1

    def test_clear_filters_preserves_page_size(self) -> None:
        """clear_filters preserves page_size setting."""
        state = FilterState(page_size=50)
        cleared = state.clear_filters()

        assert cleared.page_size == 50

    def test_clear_filters_returns_new_instance(self) -> None:
        """clear_filters returns a new instance."""
        state = FilterState(strategy="SMA")
        cleared = state.clear_filters()

        assert state is not cleared
        assert state.strategy == "SMA"  # Original unchanged


class TestSortableColumn:
    """Test SortableColumn model."""

    def test_sortable_column_creation(self) -> None:
        """SortableColumn can be created with required fields."""
        col = SortableColumn(
            name=SortColumn.SHARPE_RATIO,
            label="Sharpe Ratio",
        )
        assert col.name == SortColumn.SHARPE_RATIO
        assert col.label == "Sharpe Ratio"
        assert col.css_class == "text-left"  # default
        assert col.is_current is False
        assert col.sort_indicator == ""
        assert col.next_sort_url == ""

    def test_sortable_column_with_indicator(self) -> None:
        """SortableColumn with sort indicator."""
        col = SortableColumn(
            name=SortColumn.TOTAL_RETURN,
            label="Return",
            css_class="text-right",
            is_current=True,
            sort_indicator="▼",
            next_sort_url="/backtests/fragment?sort=total_return&order=asc",
        )
        assert col.is_current is True
        assert col.sort_indicator == "▼"


class TestPaginationControl:
    """Test PaginationControl model."""

    def test_pagination_control_creation(self) -> None:
        """PaginationControl can be created."""
        ctrl = PaginationControl(
            page_number=3,
            url="/backtests/fragment?page=3",
            is_current=True,
            label="3",
        )
        assert ctrl.page_number == 3
        assert ctrl.is_current is True
        assert ctrl.is_disabled is False
        assert ctrl.label == "3"

    def test_disabled_pagination_control(self) -> None:
        """PaginationControl can be disabled."""
        ctrl = PaginationControl(
            page_number=0,
            url="",
            is_disabled=True,
            label="Previous",
        )
        assert ctrl.is_disabled is True
