"""
Custom exceptions for database and backtest storage operations.

This module defines a hierarchy of exceptions for handling various error
conditions in backtest persistence and retrieval.
"""


class BacktestStorageError(Exception):
    """
    Base exception for backtest storage errors.

    All custom storage-related exceptions inherit from this class.
    """

    pass


class ValidationError(BacktestStorageError):
    """
    Raised when data validation fails.

    Examples:
        - Metric contains NaN or Infinity
        - Invalid configuration structure
        - Missing required fields
    """

    pass


class DatabaseConnectionError(BacktestStorageError):
    """
    Raised when database connection fails.

    Examples:
        - Cannot connect to PostgreSQL
        - Connection timeout
        - Authentication failure
    """

    pass


class DuplicateRecordError(BacktestStorageError):
    """
    Raised when attempting to create a duplicate record.

    Examples:
        - run_id already exists in database
        - Unique constraint violation
    """

    pass


class RecordNotFoundError(BacktestStorageError):
    """
    Raised when queried record doesn't exist.

    Examples:
        - Backtest run_id not found
        - No records match query filters
    """

    pass


class InstrumentMappingError(Exception):
    """Raised when a ticker cannot be mapped to a Nautilus InstrumentId."""

    pass


class InvalidSessionTransition(BacktestStorageError):
    """
    Raised when a trading-session status change cannot proceed.

    Examples:
        - An illegal edge, such as ``created`` directly to ``sealed``, or any
          transition attempted out of the terminal ``sealed`` state.
        - A refused reclaim: a transition to ``running`` requested against a
          session whose ``last_heartbeat_at`` is still fresh, meaning another
          process appears to be live on it.
    """

    pass
