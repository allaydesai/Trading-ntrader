"""Tests for export validation and error handling."""

import pytest
from decimal import Decimal
from datetime import datetime
from tempfile import TemporaryDirectory
from pathlib import Path

from src.services.reports.validators import DataValidator, FileValidator, TradeValidator
from src.services.reports.exceptions import (
    InvalidDataError,
    EmptyDataError,
    ValidationError,
)
from src.models.trade import TradeModel


class TestDataValidator:
    """Test DataValidator functionality."""

    @pytest.mark.unit
    def test_validate_non_empty_success(self):
        """Test successful validation of non-empty data."""
        DataValidator.validate_non_empty([1, 2, 3], "test_list")
        DataValidator.validate_non_empty({"key": "value"}, "test_dict")
        DataValidator.validate_non_empty("text", "test_string")

    @pytest.mark.unit
    def test_validate_non_empty_fails_on_none(self):
        """Test validation fails on None data."""
        with pytest.raises(EmptyDataError) as exc_info:
            DataValidator.validate_non_empty(None, "test_data")
        assert "test_data" in str(exc_info.value)

    @pytest.mark.unit
    def test_validate_non_empty_fails_on_empty_list(self):
        """Test validation fails on empty list."""
        with pytest.raises(EmptyDataError):
            DataValidator.validate_non_empty([], "test_list")

    @pytest.mark.unit
    def test_validate_decimal_success(self):
        """Test successful decimal validation."""
        result = DataValidator.validate_decimal(Decimal("123.45"), "price")
        assert result == Decimal("123.45")

        result = DataValidator.validate_decimal("123.45", "price")
        assert result == Decimal("123.45")

        result = DataValidator.validate_decimal(123.45, "price")
        assert result == Decimal("123.45")

    @pytest.mark.unit
    def test_validate_decimal_handles_none(self):
        """Test decimal validation handles None."""
        result = DataValidator.validate_decimal(None, "price")
        assert result is None

    @pytest.mark.unit
    def test_validate_decimal_fails_on_invalid(self):
        """Test decimal validation fails on invalid input."""
        with pytest.raises(InvalidDataError) as exc_info:
            DataValidator.validate_decimal("invalid", "price")
        assert "price" in str(exc_info.value)
        assert "Decimal" in str(exc_info.value)

    @pytest.mark.unit
    def test_validate_datetime_success(self):
        """Test successful datetime validation."""
        dt = datetime(2024, 1, 1, 12, 0)
        result = DataValidator.validate_datetime(dt, "timestamp")
        assert result == dt

        result = DataValidator.validate_datetime("2024-01-01T12:00:00", "timestamp")
        assert isinstance(result, datetime)

    @pytest.mark.unit
    def test_validate_datetime_handles_none(self):
        """Test datetime validation handles None."""
        result = DataValidator.validate_datetime(None, "timestamp")
        assert result is None

    @pytest.mark.unit
    def test_validate_datetime_fails_on_invalid(self):
        """Test datetime validation fails on invalid input."""
        with pytest.raises(InvalidDataError):
            DataValidator.validate_datetime("invalid", "timestamp")

    @pytest.mark.unit
    def test_validate_string_success(self):
        """Test successful string validation."""
        result = DataValidator.validate_string("test", "name")
        assert result == "test"

        result = DataValidator.validate_string(123, "name")
        assert result == "123"

    @pytest.mark.unit
    def test_validate_string_max_length(self):
        """Test string validation with max length."""
        DataValidator.validate_string("short", "name", max_length=10)

        with pytest.raises(InvalidDataError):
            DataValidator.validate_string("very long string", "name", max_length=5)

    @pytest.mark.unit
    def test_validate_numeric_success(self):
        """Test successful numeric validation."""
        result = DataValidator.validate_numeric(123, "count")
        assert result == 123

        result = DataValidator.validate_numeric(123.45, "price")
        assert result == 123.45

        result = DataValidator.validate_numeric(Decimal("123.45"), "price")
        assert result == 123.45

    @pytest.mark.unit
    def test_validate_numeric_with_range(self):
        """Test numeric validation with min/max values."""
        DataValidator.validate_numeric(50, "value", min_value=0, max_value=100)

        with pytest.raises(InvalidDataError):
            DataValidator.validate_numeric(-10, "value", min_value=0)

        with pytest.raises(InvalidDataError):
            DataValidator.validate_numeric(150, "value", max_value=100)


class TestFileValidator:
    """Test FileValidator functionality."""

    @pytest.mark.unit
    def test_validate_output_directory_success(self):
        """Test successful directory validation."""
        with TemporaryDirectory() as temp_dir:
            result = FileValidator.validate_output_directory(temp_dir)
            assert result == Path(temp_dir)

    @pytest.mark.unit
    def test_validate_output_directory_creates_missing(self):
        """Test directory creation when missing."""
        with TemporaryDirectory() as temp_dir:
            new_dir = Path(temp_dir) / "new_directory"
            result = FileValidator.validate_output_directory(new_dir)
            assert result.exists()
            assert result.is_dir()

    @pytest.mark.unit
    def test_validate_filename_success(self):
        """Test successful filename validation."""
        valid_names = ["file.csv", "data_export.json", "report123.txt"]
        for name in valid_names:
            result = FileValidator.validate_filename(name)
            assert result == name

    @pytest.mark.unit
    def test_validate_filename_fails_on_invalid_chars(self):
        """Test filename validation fails on invalid characters."""
        invalid_names = ["file<.csv", "data>.json", "file|.txt", "file?.csv"]
        for name in invalid_names:
            with pytest.raises(InvalidDataError):
                FileValidator.validate_filename(name)

    @pytest.mark.unit
    def test_validate_filename_fails_on_empty(self):
        """Test filename validation fails on empty string."""
        with pytest.raises(InvalidDataError):
            FileValidator.validate_filename("")

    @pytest.mark.unit
    def test_validate_filename_fails_on_too_long(self):
        """Test filename validation fails on too long names."""
        long_name = "a" * 300 + ".csv"
        with pytest.raises(InvalidDataError):
            FileValidator.validate_filename(long_name)

    @pytest.mark.unit
    def test_validate_file_extension_success(self):
        """Test successful file extension validation."""
        result = FileValidator.validate_file_extension("file.csv", [".csv", ".json"])
        assert result == "file.csv"

    @pytest.mark.unit
    def test_validate_file_extension_case_insensitive(self):
        """Test file extension validation is case insensitive."""
        result = FileValidator.validate_file_extension("file.CSV", [".csv", ".json"])
        assert result == "file.CSV"

    @pytest.mark.unit
    def test_validate_file_extension_fails_on_invalid(self):
        """Test file extension validation fails on invalid extension."""
        with pytest.raises(InvalidDataError):
            FileValidator.validate_file_extension("file.txt", [".csv", ".json"])


class TestTradeValidator:
    """Test TradeValidator functionality."""

    @pytest.mark.unit
    def test_validate_trade_model_success(self):
        """Test successful trade model validation."""
        trade = TradeModel(
            position_id="POS-001",
            instrument_id="AAPL",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("155.00"),
            side="LONG",
            entry_time=datetime(2024, 1, 1),
            exit_time=datetime(2024, 1, 1),
        )

        errors = TradeValidator.validate_trade_model(trade)
        assert len(errors) == 0

    @pytest.mark.unit
    def test_validate_trade_model_missing_fields(self):
        """Test trade model validation with missing required fields."""

        # Create a mock object that mimics a trade with missing fields
        class MockTrade:
            def __init__(self):
                self.position_id = "POS-001"
                self.instrument_id = "AAPL"
                self.quantity = Decimal("100")
                self.entry_price = None  # Missing required field
                self.side = "LONG"
                self.entry_time = datetime(2024, 1, 1)

        trade = MockTrade()
        errors = TradeValidator.validate_trade_model(trade)
        assert len(errors) > 0
        assert any("entry_price" in error for error in errors)

    @pytest.mark.unit
    def test_validate_trade_model_invalid_prices(self):
        """Test trade model validation with invalid prices."""

        # Create a mock object that bypasses Pydantic validation
        class MockTrade:
            def __init__(self):
                self.position_id = "POS-001"
                self.instrument_id = "AAPL"
                self.quantity = Decimal("100")
                self.entry_price = Decimal("-150.00")  # Invalid negative price
                self.side = "LONG"
                self.entry_time = datetime(2024, 1, 1)

        trade = MockTrade()
        errors = TradeValidator.validate_trade_model(trade)
        assert len(errors) > 0
        assert any("positive" in error for error in errors)

    @pytest.mark.unit
    def test_validate_trade_model_invalid_side(self):
        """Test trade model validation with invalid side."""

        # Create a mock object that bypasses Pydantic validation
        class MockTrade:
            def __init__(self):
                self.position_id = "POS-001"
                self.instrument_id = "AAPL"
                self.quantity = Decimal("100")
                self.entry_price = Decimal("150.00")
                self.side = "INVALID"  # Invalid side
                self.entry_time = datetime(2024, 1, 1)

        trade = MockTrade()
        errors = TradeValidator.validate_trade_model(trade)
        assert len(errors) > 0
        assert any("Invalid side" in error for error in errors)

    @pytest.mark.unit
    def test_validate_trade_list_success(self):
        """Test successful trade list validation."""
        trades = [
            TradeModel(
                position_id="POS-001",
                instrument_id="AAPL",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                side="LONG",
                entry_time=datetime(2024, 1, 1),
            ),
            TradeModel(
                position_id="POS-002",
                instrument_id="GOOGL",
                quantity=Decimal("50"),
                entry_price=Decimal("2800.00"),
                side="LONG",
                entry_time=datetime(2024, 1, 2),
            ),
        ]

        # Should not raise exception
        TradeValidator.validate_trade_list(trades)

    @pytest.mark.unit
    def test_validate_trade_list_empty(self):
        """Test trade list validation fails on empty list."""
        with pytest.raises(EmptyDataError):
            TradeValidator.validate_trade_list([])

    @pytest.mark.unit
    def test_validate_trade_list_none(self):
        """Test trade list validation fails on None."""
        with pytest.raises(EmptyDataError):
            TradeValidator.validate_trade_list(None)

    @pytest.mark.unit
    def test_validate_trade_list_with_invalid_trades(self):
        """Test trade list validation fails with invalid trades."""

        # Create a mock object that bypasses Pydantic validation
        class MockTrade:
            def __init__(self, position_id, entry_price, side):
                self.position_id = position_id
                self.instrument_id = "GOOGL"
                self.quantity = Decimal("50")
                self.entry_price = entry_price
                self.side = side
                self.entry_time = datetime(2024, 1, 2)

        trades = [
            TradeModel(
                position_id="POS-001",
                instrument_id="AAPL",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                side="LONG",
                entry_time=datetime(2024, 1, 1),
            ),
            MockTrade("POS-002", Decimal("-2800.00"), "INVALID"),  # Invalid trade
        ]

        with pytest.raises(ValidationError) as exc_info:
            TradeValidator.validate_trade_list(trades)

        assert "Trade 2:" in str(exc_info.value)
        assert "positive" in str(exc_info.value)
        assert "Invalid side" in str(exc_info.value)
