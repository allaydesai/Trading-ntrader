"""Error message templates for Trading-NTrader system.

This module provides structured error message templates with actionable
resolution steps for all error scenarios in the application.

Architecture:
- ErrorSeverity enum: Categorizes error criticality
- ErrorCategory enum: Groups errors by functional domain
- ErrorMessage dataclass: Structured error information
- Message templates: Pre-defined messages for common scenarios
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ErrorSeverity(str, Enum):
    """Error severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(str, Enum):
    """Error categories for grouping related errors."""

    DATA = "data"
    CONNECTION = "connection"
    INPUT = "input"
    SYSTEM = "system"
    RATE_LIMIT = "rate_limit"
    CORRUPTION = "corruption"


@dataclass
class ErrorMessage:
    """Structured error message with actionable information.

    Attributes:
        category: Error category for classification
        severity: Error severity level
        title: Short error title
        message: Detailed error description
        resolution_steps: List of actionable steps to resolve
        technical_details: Optional technical information
    """

    category: ErrorCategory
    severity: ErrorSeverity
    title: str
    message: str
    resolution_steps: list[str]
    technical_details: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert error message to dictionary format."""
        return {
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "resolution_steps": self.resolution_steps,
            "technical_details": self.technical_details,
            "timestamp": datetime.utcnow().isoformat(),
        }


# Data Not Found Error Messages
DATA_NOT_FOUND_NO_IBKR = ErrorMessage(
    category=ErrorCategory.DATA,
    severity=ErrorSeverity.ERROR,
    title="Market Data Not Available",
    message=(
        "The requested market data is not in the Parquet catalog "
        "and cannot be fetched because IBKR connection is unavailable."
    ),
    resolution_steps=[
        "Verify IBKR Gateway/TWS is running: docker compose up ibgateway",
        "Check IBKR connection status in the application logs",
        "Ensure your IBKR account has data subscription for this symbol",
        "Try importing data from CSV: ntrader data import <csv_file>",
        "Check if the requested date range is within IBKR data limits",
    ],
    technical_details=None,
)

DATA_NOT_FOUND_PARTIAL = ErrorMessage(
    category=ErrorCategory.DATA,
    severity=ErrorSeverity.WARNING,
    title="Partial Data Coverage",
    message=(
        "Only partial data is available in the catalog for the requested "
        "date range. Some data gaps exist."
    ),
    resolution_steps=[
        "Run 'ntrader data check' to see exact date coverage",
        "Use 'ntrader data list' to view available instruments and ranges",
        "Run backtest anyway to trigger auto-fetch for missing dates",
        "Adjust backtest start/end dates to match available data",
    ],
)

# IBKR Connection Error Messages
IBKR_CONNECTION_FAILED = ErrorMessage(
    category=ErrorCategory.CONNECTION,
    severity=ErrorSeverity.ERROR,
    title="IBKR Connection Failed",
    message=(
        "Unable to connect to Interactive Brokers Gateway or TWS. "
        "The connection is required to fetch missing market data."
    ),
    resolution_steps=[
        "Start IBKR Gateway: docker compose up ibgateway",
        "Verify Gateway is running: docker compose ps",
        "Check Gateway logs: docker compose logs ibgateway",
        "Ensure port 4001 is not blocked by firewall",
        "Verify IBKR credentials are correct in .env file",
        "Wait 30-60 seconds after starting Gateway before retrying",
    ],
    technical_details=None,
)

IBKR_CONNECTION_TIMEOUT = ErrorMessage(
    category=ErrorCategory.CONNECTION,
    severity=ErrorSeverity.ERROR,
    title="IBKR Connection Timeout",
    message=(
        "Connection to IBKR Gateway timed out. The Gateway may be "
        "unresponsive or network issues may be present."
    ),
    resolution_steps=[
        "Restart IBKR Gateway: docker compose restart ibgateway",
        "Check network connectivity",
        "Verify Gateway is accepting connections on port 4001",
        "Check if other applications are using the same Gateway instance",
    ],
)

# Rate Limit Error Messages
RATE_LIMIT_EXCEEDED = ErrorMessage(
    category=ErrorCategory.RATE_LIMIT,
    severity=ErrorSeverity.WARNING,
    title="IBKR Rate Limit Exceeded",
    message=(
        "The application has exceeded IBKR's rate limit of 50 requests "
        "per second. The fetch operation will be retried with backoff."
    ),
    resolution_steps=[
        "Wait for automatic retry with exponential backoff",
        "Reduce concurrent data fetch operations",
        "Consider importing data from CSV for bulk operations",
        "Press Ctrl+C to cancel the operation if needed",
    ],
    technical_details="IBKR enforces 50 req/sec limit. Using 45 req/sec.",
)

# Catalog Corruption Error Messages
CATALOG_CORRUPTION_DETECTED = ErrorMessage(
    category=ErrorCategory.CORRUPTION,
    severity=ErrorSeverity.CRITICAL,
    title="Parquet Catalog Corruption Detected",
    message=(
        "One or more Parquet files in the catalog are corrupted and "
        "cannot be read. The files will be quarantined automatically."
    ),
    resolution_steps=[
        "Corrupted files moved to data/catalog/.corrupt/ directory",
        "Check .corrupt/ directory to review problematic files",
        "Re-fetch data to replace corrupted files",
        "If corruption persists, check disk health: df -h",
        "Consider restoring from backup if available",
    ],
    technical_details=None,
)

CATALOG_CORRUPTION_QUARANTINE_FAILED = ErrorMessage(
    category=ErrorCategory.SYSTEM,
    severity=ErrorSeverity.CRITICAL,
    title="Failed to Quarantine Corrupted Files",
    message=(
        "Unable to move corrupted Parquet files to quarantine directory. "
        "Check filesystem permissions."
    ),
    resolution_steps=[
        "Check filesystem permissions: ls -la data/catalog/",
        "Ensure write access to data/catalog/ directory",
        "Manually move corrupted files to backup location",
        "Check available disk space: df -h",
    ],
)

# Input Validation Error Messages
INVALID_DATE_RANGE = ErrorMessage(
    category=ErrorCategory.INPUT,
    severity=ErrorSeverity.ERROR,
    title="Invalid Date Range",
    message="The specified date range is invalid or end date is before start.",
    resolution_steps=[
        "Ensure start date is before end date",
        "Use ISO format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS",
        "Check for timezone issues (all times should be UTC)",
        "Verify dates are not in the future",
    ],
)

INVALID_INSTRUMENT = ErrorMessage(
    category=ErrorCategory.INPUT,
    severity=ErrorSeverity.ERROR,
    title="Invalid Instrument Specification",
    message="The specified instrument ID or symbol is not recognized.",
    resolution_steps=[
        "Check instrument format: SYMBOL.EXCHANGE (e.g., AAPL.NASDAQ)",
        "Verify the exchange code is correct",
        "Use 'ntrader data list' to see available instruments",
        "Ensure IBKR supports the specified symbol",
    ],
)

# System Error Messages
DISK_SPACE_LOW = ErrorMessage(
    category=ErrorCategory.SYSTEM,
    severity=ErrorSeverity.WARNING,
    title="Low Disk Space Warning",
    message="Available disk space is running low. Catalog operations may fail.",
    resolution_steps=[
        "Check disk space: df -h",
        "Clean up old log files or temporary files",
        "Archive or delete old Parquet data if no longer needed",
        "Consider expanding disk storage",
    ],
)

PERMISSION_DENIED = ErrorMessage(
    category=ErrorCategory.SYSTEM,
    severity=ErrorSeverity.ERROR,
    title="Permission Denied",
    message="Insufficient permissions to access catalog directory or files.",
    resolution_steps=[
        "Check directory permissions: ls -la data/catalog/",
        "Ensure current user has read/write access",
        "Fix permissions: chmod -R u+rw data/catalog/",
        "Verify file ownership is correct",
    ],
)


def format_error_with_context(
    error_template: ErrorMessage,
    **context_vars: str | int | float | None,
) -> ErrorMessage:
    """Format error message with context-specific variables.

    Args:
        error_template: Base error message template
        **context_vars: Variables to interpolate into message

    Returns:
        ErrorMessage with formatted strings

    Example:
        >>> error = format_error_with_context(
        ...     DATA_NOT_FOUND_NO_IBKR,
        ...     instrument="AAPL.NASDAQ",
        ...     start_date="2024-01-01",
        ...     end_date="2024-12-31"
        ... )
    """
    formatted_message = error_template.message
    formatted_details = error_template.technical_details

    # Format message with context variables
    if context_vars:
        try:
            formatted_message = error_template.message.format(**context_vars)
            if error_template.technical_details:
                formatted_details = error_template.technical_details.format(**context_vars)
        except KeyError as e:
            # If formatting fails, append context as technical details
            formatted_details = f"Context: {context_vars} | Format error: {e}"

    return ErrorMessage(
        category=error_template.category,
        severity=error_template.severity,
        title=error_template.title,
        message=formatted_message,
        resolution_steps=error_template.resolution_steps,
        technical_details=formatted_details,
    )
