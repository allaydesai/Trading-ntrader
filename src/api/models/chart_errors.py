"""
Error response models for Chart APIs.

Provides structured error responses with actionable suggestions
for CLI commands when data is not found.
"""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """
    Error response with actionable suggestions.

    Used for 404 responses to provide users with CLI commands
    to fetch missing data.

    Attributes:
        detail: Error message describing what went wrong
        suggestion: Optional CLI command hint for resolution

    Example:
        >>> error = ErrorDetail(
        ...     detail="Market data not found for AAPL",
        ...     suggestion="Run: ntrader data fetch --symbol AAPL"
        ... )
    """

    detail: str
    suggestion: str | None = None


class ValidationErrorDetail(BaseModel):
    """
    Validation error response.

    Used for 422 responses with Pydantic validation error details.

    Attributes:
        detail: Error summary message
        errors: List of validation error dictionaries

    Example:
        >>> error = ValidationErrorDetail(
        ...     detail="Validation failed",
        ...     errors=[{"loc": ["query", "end"], "msg": "invalid date"}]
        ... )
    """

    detail: str
    errors: list[dict]
