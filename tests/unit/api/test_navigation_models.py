"""Unit tests for navigation view models."""

import pytest
from pydantic import ValidationError

from src.api.models.navigation import BreadcrumbItem, NavigationState


class TestNavigationState:
    """Tests for NavigationState active_page validation."""

    @pytest.mark.unit
    def test_explorer_is_valid_active_page(self):
        nav = NavigationState(active_page="explorer")
        assert nav.active_page == "explorer"

    @pytest.mark.unit
    def test_dashboard_still_valid(self):
        nav = NavigationState(active_page="dashboard")
        assert nav.active_page == "dashboard"

    @pytest.mark.unit
    def test_invalid_page_rejected(self):
        with pytest.raises(ValidationError):
            NavigationState(active_page="nonexistent")

    @pytest.mark.unit
    def test_explorer_with_breadcrumbs(self):
        nav = NavigationState(
            active_page="explorer",
            breadcrumbs=[
                BreadcrumbItem(label="Explorer", url="/explorer", is_current=False),
                BreadcrumbItem(label="us_stocks", url=None, is_current=True),
            ],
        )
        assert len(nav.breadcrumbs) == 2
        assert nav.breadcrumbs[1].label == "us_stocks"
