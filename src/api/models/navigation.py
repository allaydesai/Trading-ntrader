"""
Navigation view models for web UI rendering.

Provides data structures for navigation state, breadcrumbs, and page context.
"""

from typing import Optional

from pydantic import BaseModel, Field


class BreadcrumbItem(BaseModel):
    """
    Single item in breadcrumb navigation trail.

    Attributes:
        label: Display text for the breadcrumb
        url: Link URL (None for current page)
        is_current: Whether this is the current page (last item)

    Example:
        >>> item = BreadcrumbItem(label="Dashboard", url="/", is_current=False)
        >>> current = BreadcrumbItem(label="Backtests", url=None, is_current=True)
    """

    label: str = Field(..., min_length=1, max_length=50, description="Display text")
    url: Optional[str] = Field(None, description="Link URL (None for current page)")
    is_current: bool = Field(False, description="Whether this is the current page")


class NavigationState(BaseModel):
    """
    Current navigation context for template rendering.

    Provides the active page identifier and breadcrumb trail for consistent
    navigation highlighting across all pages.

    Attributes:
        active_page: Current page identifier (e.g., "dashboard", "backtests")
        breadcrumbs: List of breadcrumb items for navigation trail
        app_version: Application version string

    Example:
        >>> nav = NavigationState(
        ...     active_page="backtests",
        ...     breadcrumbs=[
        ...         BreadcrumbItem(label="Dashboard", url="/", is_current=False),
        ...         BreadcrumbItem(label="Backtests", url=None, is_current=True)
        ...     ],
        ...     app_version="0.1.0"
        ... )
    """

    active_page: str = Field(
        ...,
        description="Current page identifier",
        pattern=r"^(dashboard|backtests|data|docs)$",
    )
    breadcrumbs: list[BreadcrumbItem] = Field(
        default_factory=list, description="Navigation trail"
    )
    app_version: str = Field("0.1.0", description="Application version")
