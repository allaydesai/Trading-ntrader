"""Custom exceptions for export services."""

from typing import Optional


class ExportError(Exception):
    """Base exception for export-related errors."""

    def __init__(self, message: str, details: Optional[str] = None):
        """Initialize export error.

        Args:
            message: Error message
            details: Additional error details
        """
        self.message = message
        self.details = details
        super().__init__(message)

    def __str__(self) -> str:
        """String representation of the error."""
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class FileWriteError(ExportError):
    """Raised when file writing operations fail."""

    def __init__(self, filepath: str, reason: str):
        """Initialize file write error.

        Args:
            filepath: Path to file that failed to write
            reason: Reason for the write failure
        """
        self.filepath = filepath
        self.reason = reason
        super().__init__(
            f"Failed to write to file: {filepath}",
            details=reason
        )


class InvalidDataError(ExportError):
    """Raised when data validation fails before export."""

    def __init__(self, field_name: str, value: str, expected_type: str):
        """Initialize invalid data error.

        Args:
            field_name: Name of the invalid field
            value: The invalid value
            expected_type: Expected data type
        """
        self.field_name = field_name
        self.value = value
        self.expected_type = expected_type
        super().__init__(
            f"Invalid data in field '{field_name}'",
            details=f"Expected {expected_type}, got '{value}'"
        )


class EmptyDataError(ExportError):
    """Raised when attempting to export empty or None data."""

    def __init__(self, data_type: str):
        """Initialize empty data error.

        Args:
            data_type: Type of data that was empty
        """
        self.data_type = data_type
        super().__init__(f"Cannot export empty {data_type}")


class UnsupportedFormatError(ExportError):
    """Raised when an unsupported export format is requested."""

    def __init__(self, format_name: str, supported_formats: list):
        """Initialize unsupported format error.

        Args:
            format_name: The unsupported format that was requested
            supported_formats: List of supported formats
        """
        self.format_name = format_name
        self.supported_formats = supported_formats
        super().__init__(
            f"Unsupported export format: {format_name}",
            details=f"Supported formats: {', '.join(supported_formats)}"
        )


class SerializationError(ExportError):
    """Raised when data serialization fails."""

    def __init__(self, data_type: str, reason: str):
        """Initialize serialization error.

        Args:
            data_type: Type of data that failed to serialize
            reason: Reason for serialization failure
        """
        self.data_type = data_type
        self.reason = reason
        super().__init__(
            f"Failed to serialize {data_type}",
            details=reason
        )


class DirectoryError(ExportError):
    """Raised when directory operations fail."""

    def __init__(self, directory_path: str, operation: str, reason: str):
        """Initialize directory error.

        Args:
            directory_path: Path to the directory
            operation: The operation that failed (create, access, etc.)
            reason: Reason for the failure
        """
        self.directory_path = directory_path
        self.operation = operation
        self.reason = reason
        super().__init__(
            f"Directory {operation} failed: {directory_path}",
            details=reason
        )


class PermissionError(ExportError):
    """Raised when file/directory permission issues occur."""

    def __init__(self, path: str, required_permission: str):
        """Initialize permission error.

        Args:
            path: Path that has permission issues
            required_permission: Required permission (read, write, execute)
        """
        self.path = path
        self.required_permission = required_permission
        super().__init__(
            f"Permission denied: {required_permission} access to {path}"
        )


class ValidationError(ExportError):
    """Raised when data validation fails."""

    def __init__(self, validation_type: str, errors: list):
        """Initialize validation error.

        Args:
            validation_type: Type of validation that failed
            errors: List of validation error messages
        """
        self.validation_type = validation_type
        self.errors = errors
        error_summary = "; ".join(errors[:3])  # Show first 3 errors
        if len(errors) > 3:
            error_summary += f" (and {len(errors) - 3} more)"

        super().__init__(
            f"{validation_type} validation failed",
            details=error_summary
        )