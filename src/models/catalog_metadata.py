"""
Pydantic models for Parquet catalog metadata.

This module defines data models for tracking catalog availability,
fetch requests, and related metadata structures.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class CatalogAvailability(BaseModel):
    """
    Metadata for catalog data availability.

    Tracks which date ranges are available in the Parquet catalog
    for a specific instrument and bar type combination.

    Attributes:
        instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")
        bar_type_spec: Bar type specification (e.g., "1-MINUTE-LAST")
        start_date: Earliest available data timestamp (UTC)
        end_date: Latest available data timestamp (UTC)
        file_count: Number of Parquet files in this range
        total_rows: Approximate total number of bars
        last_updated: Last time this metadata was updated (UTC)
    """

    instrument_id: str = Field(..., min_length=1, max_length=50)
    bar_type_spec: str = Field(..., min_length=1, max_length=50)
    start_date: datetime
    end_date: datetime
    file_count: int = Field(..., ge=1)
    total_rows: int = Field(..., ge=0)
    last_updated: datetime

    @field_validator("start_date", "end_date", "last_updated")
    @classmethod
    def ensure_utc_timezone(cls, v: datetime) -> datetime:
        """
        Ensure all timestamps are UTC timezone-aware.

        Args:
            v: Input datetime value

        Returns:
            UTC timezone-aware datetime

        Example:
            >>> from datetime import datetime, timezone
            >>> dt = datetime(2024, 1, 1)
            >>> CatalogAvailability.ensure_utc_timezone(dt)
            datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
        """
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("end_date")
    @classmethod
    def validate_date_range(cls, v: datetime, info) -> datetime:
        """
        Validate that end_date is greater than or equal to start_date.

        Args:
            v: End date value
            info: Validation context containing other field values

        Returns:
            Validated end date

        Raises:
            ValueError: If end_date < start_date
        """
        if "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError("end_date must be >= start_date")
        return v

    def covers_range(self, start: datetime, end: datetime) -> bool:
        """
        Check if this availability fully covers the requested range.

        For DAY-level data, compares only the date portion to avoid timezone/time-of-day issues.
        For intraday data (MINUTE, HOUR), compares full timestamps.

        Args:
            start: Requested start date
            end: Requested end date

        Returns:
            True if catalog covers the entire requested range

        Example:
            >>> avail = CatalogAvailability(
            ...     instrument_id="AAPL.NASDAQ",
            ...     bar_type_spec="1-MINUTE-LAST",
            ...     start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ...     end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            ...     file_count=252,
            ...     total_rows=100000,
            ...     last_updated=datetime.now(timezone.utc)
            ... )
            >>> avail.covers_range(
            ...     datetime(2024, 2, 1, tzinfo=timezone.utc),
            ...     datetime(2024, 2, 28, tzinfo=timezone.utc)
            ... )
            True
        """
        # Reason: For DAY-level data, compare dates only (ignore time portion)
        # This prevents false negatives when catalog has 23:59:59 but request is 00:00:00
        if "DAY" in self.bar_type_spec or "WEEK" in self.bar_type_spec:
            return self.start_date.date() <= start.date() and self.end_date.date() >= end.date()

        # Reason: For intraday data, compare full timestamps
        return self.start_date <= start and self.end_date >= end

    def overlaps_range(self, start: datetime, end: datetime) -> bool:
        """
        Check if this availability overlaps with the requested range.

        Args:
            start: Requested start date
            end: Requested end date

        Returns:
            True if there is any overlap between catalog and requested range

        Example:
            >>> avail.overlaps_range(
            ...     datetime(2023, 12, 15, tzinfo=timezone.utc),
            ...     datetime(2024, 1, 15, tzinfo=timezone.utc)
            ... )
            True
        """
        return not (self.end_date < start or self.start_date > end)


class FetchStatus(str, Enum):
    """Status of a data fetch request."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class FetchRequest(BaseModel):
    """
    Track IBKR historical data fetch operations.

    This model represents a request to fetch historical bars from IBKR,
    tracking its progress, retry attempts, and completion status.

    Attributes:
        request_id: Unique request identifier
        instrument_id: Instrument to fetch (e.g., "AAPL.NASDAQ")
        bar_type_spec: Bar type specification (e.g., "1-MINUTE-LAST")
        start_date: Fetch start date (UTC)
        end_date: Fetch end date (UTC)
        status: Current request status
        retry_count: Number of retry attempts made
        error_message: Error details if status is FAILED
        created_at: Request creation timestamp (UTC)
        completed_at: Request completion timestamp (UTC)
    """

    request_id: UUID = Field(default_factory=uuid4)
    instrument_id: str = Field(..., min_length=1)
    bar_type_spec: str = Field(..., min_length=1)
    start_date: datetime
    end_date: datetime
    status: FetchStatus = FetchStatus.PENDING
    retry_count: int = Field(default=0, ge=0, le=5)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def mark_in_progress(self) -> None:
        """
        Transition request to IN_PROGRESS status.

        Example:
            >>> request = FetchRequest(
            ...     instrument_id="AAPL.NASDAQ",
            ...     bar_type_spec="1-MINUTE-LAST",
            ...     start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ...     end_date=datetime(2024, 1, 31, tzinfo=timezone.utc)
            ... )
            >>> request.mark_in_progress()
            >>> request.status
            <FetchStatus.IN_PROGRESS: 'in_progress'>
        """
        self.status = FetchStatus.IN_PROGRESS

    def mark_completed(self) -> None:
        """
        Transition request to COMPLETED status and set completion time.

        Example:
            >>> request.mark_completed()
            >>> request.status
            <FetchStatus.COMPLETED: 'completed'>
            >>> request.completed_at is not None
            True
        """
        self.status = FetchStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        """
        Transition request to FAILED status and increment retry count.

        Args:
            error: Error message describing the failure

        Example:
            >>> request.mark_failed("Connection timeout")
            >>> request.status
            <FetchStatus.FAILED: 'failed'>
            >>> request.error_message
            'Connection timeout'
            >>> request.retry_count
            1
        """
        self.status = FetchStatus.FAILED
        self.error_message = error
        self.retry_count += 1
