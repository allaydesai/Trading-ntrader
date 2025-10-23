"""
Custom exceptions for catalog operations.

This module defines a hierarchy of exceptions for Parquet catalog
operations, IBKR data fetching, and data validation errors.
"""

from datetime import datetime


class CatalogError(Exception):
    """Base exception for all catalog-related operations."""

    pass


class DataNotFoundError(CatalogError):
    """
    Raised when requested data is not found in catalog.

    Attributes:
        instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")
        start: Start date of requested range
        end: End date of requested range
    """

    def __init__(self, instrument_id: str, start: datetime, end: datetime) -> None:
        """
        Initialize DataNotFoundError.

        Args:
            instrument_id: Instrument identifier
            start: Start date of requested range
            end: End date of requested range
        """
        self.instrument_id = instrument_id
        self.start = start
        self.end = end
        super().__init__(
            f"Data not found: {instrument_id} from "
            f"{start.isoformat()} to {end.isoformat()}"
        )


class IBKRConnectionError(CatalogError):
    """
    Raised when IBKR connection is unavailable during fetch operation.

    This exception indicates that the IBKR Gateway or TWS is not running
    or not accessible when attempting to fetch historical data.
    """

    def __init__(self, message: str = "IBKR connection unavailable") -> None:
        """
        Initialize IBKRConnectionError.

        Args:
            message: Error message describing the connection issue
        """
        super().__init__(message)


class CatalogCorruptionError(CatalogError):
    """
    Raised when a Parquet file in the catalog is corrupted or unreadable.

    Attributes:
        file_path: Path to the corrupted file
        original_error: The underlying exception that caused the error
    """

    def __init__(self, file_path: str, original_error: Exception) -> None:
        """
        Initialize CatalogCorruptionError.

        Args:
            file_path: Path to the corrupted Parquet file
            original_error: The underlying exception
        """
        self.file_path = file_path
        self.original_error = original_error
        super().__init__(
            f"Corrupted catalog file: {file_path} (Error: {original_error})"
        )


class RateLimitExceededError(CatalogError):
    """
    Raised when IBKR rate limit is exceeded during data fetch.

    IBKR enforces a rate limit of 50 requests per second. This exception
    is raised when the limit is exceeded and retry is required.

    Attributes:
        retry_after: Seconds to wait before retrying
        request_count: Number of requests made
    """

    def __init__(self, retry_after: int = 2, request_count: int | None = None) -> None:
        """
        Initialize RateLimitExceededError.

        Args:
            retry_after: Seconds to wait before retrying
            request_count: Number of requests made (optional)
        """
        self.retry_after = retry_after
        self.request_count = request_count
        message = f"IBKR rate limit exceeded. Retry after {retry_after} seconds."
        if request_count:
            message += f" (Requests: {request_count})"
        super().__init__(message)
