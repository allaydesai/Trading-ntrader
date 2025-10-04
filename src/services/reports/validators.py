"""Validation utilities for export services."""

import os
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Union

from .exceptions import (
    InvalidDataError,
    EmptyDataError,
    DirectoryError,
    PermissionError as ExportPermissionError,
    ValidationError,
)


class DataValidator:
    """Validates data before export operations."""

    @staticmethod
    def validate_non_empty(data: Any, data_type: str) -> None:
        """Validate that data is not empty or None.

        Args:
            data: Data to validate
            data_type: Type description for error messages

        Raises:
            EmptyDataError: If data is empty or None
        """
        if data is None:
            raise EmptyDataError(data_type)

        if hasattr(data, "__len__") and len(data) == 0:
            raise EmptyDataError(data_type)

    @staticmethod
    def validate_decimal(value: Any, field_name: str) -> Decimal:
        """Validate and convert value to Decimal.

        Args:
            value: Value to validate
            field_name: Name of the field for error messages

        Returns:
            Validated Decimal value

        Raises:
            InvalidDataError: If value cannot be converted to Decimal
        """
        if value is None:
            return None

        try:
            if isinstance(value, Decimal):
                return value
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise InvalidDataError(field_name, str(value), "Decimal") from e

    @staticmethod
    def validate_datetime(value: Any, field_name: str) -> datetime:
        """Validate datetime value.

        Args:
            value: Value to validate
            field_name: Name of the field for error messages

        Returns:
            Validated datetime value

        Raises:
            InvalidDataError: If value is not a valid datetime
        """
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        # Try to parse string datetime
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as e:
                raise InvalidDataError(field_name, str(value), "datetime") from e

        raise InvalidDataError(field_name, str(value), "datetime")

    @staticmethod
    def validate_string(
        value: Any, field_name: str, max_length: Optional[int] = None
    ) -> str:
        """Validate string value.

        Args:
            value: Value to validate
            field_name: Name of the field for error messages
            max_length: Maximum allowed length

        Returns:
            Validated string value

        Raises:
            InvalidDataError: If value is not a valid string
        """
        if value is None:
            return None

        if not isinstance(value, str):
            try:
                value = str(value)
            except Exception as e:
                raise InvalidDataError(field_name, str(value), "string") from e

        if max_length and len(value) > max_length:
            raise InvalidDataError(
                field_name,
                f"String too long ({len(value)} chars)",
                f"string (max {max_length} chars)",
            )

        return value

    @staticmethod
    def validate_numeric(
        value: Any,
        field_name: str,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> Union[int, float]:
        """Validate numeric value.

        Args:
            value: Value to validate
            field_name: Name of the field for error messages
            min_value: Minimum allowed value
            max_value: Maximum allowed value

        Returns:
            Validated numeric value

        Raises:
            InvalidDataError: If value is not numeric or out of range
        """
        if value is None:
            return None

        try:
            if isinstance(value, (int, float)):
                numeric_value = value
            elif isinstance(value, Decimal):
                numeric_value = float(value)
            else:
                numeric_value = float(str(value))

            if min_value is not None and numeric_value < min_value:
                raise InvalidDataError(
                    field_name, str(value), f"numeric value >= {min_value}"
                )

            if max_value is not None and numeric_value > max_value:
                raise InvalidDataError(
                    field_name, str(value), f"numeric value <= {max_value}"
                )

            return numeric_value

        except (ValueError, TypeError) as e:
            raise InvalidDataError(field_name, str(value), "numeric") from e


class FileValidator:
    """Validates file and directory operations."""

    @staticmethod
    def validate_output_directory(path: Union[str, Path]) -> Path:
        """Validate output directory exists and is writable.

        Args:
            path: Directory path to validate

        Returns:
            Validated Path object

        Raises:
            DirectoryError: If directory operations fail
            ExportPermissionError: If directory is not writable
        """
        path_obj = Path(path)

        # Try to create directory if it doesn't exist
        try:
            path_obj.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DirectoryError(str(path), "create", str(e)) from e

        # Check if directory is writable
        if not os.access(path_obj, os.W_OK):
            raise ExportPermissionError(str(path), "write")

        return path_obj

    @staticmethod
    def validate_filename(filename: str) -> str:
        """Validate filename for export.

        Args:
            filename: Filename to validate

        Returns:
            Validated filename

        Raises:
            InvalidDataError: If filename is invalid
        """
        if not filename or not isinstance(filename, str):
            raise InvalidDataError("filename", str(filename), "non-empty string")

        # For absolute paths, only validate the filename part
        path_obj = Path(filename)
        filename_only = path_obj.name

        # Remove or replace invalid characters (excluding path separators for absolute paths)
        invalid_chars = '<>"|?*'  # Removed : / \ as they're valid in paths
        for char in invalid_chars:
            if char in filename_only:
                raise InvalidDataError(
                    "filename",
                    filename,
                    f"filename without characters: {invalid_chars}",
                )

        # Check filename length (just the name part, not the full path)
        if len(filename_only) > 255:
            raise InvalidDataError("filename", filename, "filename <= 255 characters")

        return filename

    @staticmethod
    def validate_file_extension(filename: str, allowed_extensions: List[str]) -> str:
        """Validate file extension.

        Args:
            filename: Filename to validate
            allowed_extensions: List of allowed extensions (e.g., ['.csv', '.json'])

        Returns:
            Validated filename

        Raises:
            InvalidDataError: If extension is not allowed
        """
        path_obj = Path(filename)
        extension = path_obj.suffix.lower()

        if extension not in [ext.lower() for ext in allowed_extensions]:
            raise InvalidDataError(
                "file_extension", extension, f"one of: {', '.join(allowed_extensions)}"
            )

        return filename


class TradeValidator:
    """Validates trade data before export."""

    @staticmethod
    def validate_trade_model(trade: Any) -> List[str]:
        """Validate a single trade model.

        Args:
            trade: Trade model to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Required fields validation
        required_fields = [
            "position_id",
            "instrument_id",
            "entry_price",
            "quantity",
            "side",
        ]
        for field in required_fields:
            if not hasattr(trade, field) or getattr(trade, field) is None:
                errors.append(f"Missing required field: {field}")

        # Validate specific field types and values
        try:
            if hasattr(trade, "entry_price") and trade.entry_price is not None:
                DataValidator.validate_decimal(trade.entry_price, "entry_price")
                if trade.entry_price <= 0:
                    errors.append("Entry price must be positive")
        except InvalidDataError as e:
            errors.append(str(e))

        try:
            if hasattr(trade, "exit_price") and trade.exit_price is not None:
                DataValidator.validate_decimal(trade.exit_price, "exit_price")
                if trade.exit_price <= 0:
                    errors.append("Exit price must be positive")
        except InvalidDataError as e:
            errors.append(str(e))

        try:
            if hasattr(trade, "quantity") and trade.quantity is not None:
                DataValidator.validate_decimal(trade.quantity, "quantity")
                if trade.quantity <= 0:
                    errors.append("Quantity must be positive")
        except InvalidDataError as e:
            errors.append(str(e))

        # Validate side
        if hasattr(trade, "side") and trade.side:
            valid_sides = ["LONG", "SHORT", "BUY", "SELL"]
            if trade.side.upper() not in valid_sides:
                errors.append(
                    f"Invalid side: {trade.side}. Must be one of: {valid_sides}"
                )

        # Validate timestamps
        try:
            if hasattr(trade, "entry_time") and trade.entry_time:
                DataValidator.validate_datetime(trade.entry_time, "entry_time")
        except InvalidDataError as e:
            errors.append(str(e))

        try:
            if hasattr(trade, "exit_time") and trade.exit_time:
                DataValidator.validate_datetime(trade.exit_time, "exit_time")
        except InvalidDataError as e:
            errors.append(str(e))

        # Validate instrument_id format
        if hasattr(trade, "instrument_id") and trade.instrument_id:
            if (
                not isinstance(trade.instrument_id, str)
                or len(trade.instrument_id.strip()) == 0
            ):
                errors.append("Instrument ID must be a non-empty string")

        return errors

    @staticmethod
    def validate_trade_list(trades: List[Any]) -> None:
        """Validate a list of trades.

        Args:
            trades: List of trade models to validate

        Raises:
            EmptyDataError: If trades list is empty
            ValidationError: If validation fails
        """
        DataValidator.validate_non_empty(trades, "trades")

        all_errors = []
        for i, trade in enumerate(trades):
            trade_errors = TradeValidator.validate_trade_model(trade)
            if trade_errors:
                for error in trade_errors:
                    all_errors.append(f"Trade {i + 1}: {error}")

        if all_errors:
            raise ValidationError("Trade data", all_errors)
