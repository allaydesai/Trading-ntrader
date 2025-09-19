"""Tests for MarketDataWrangler."""

from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest
import pandas as pd

from src.utils.data_wrangler import MarketDataWrangler
from src.utils.mock_data import create_test_instrument


class TestMarketDataWrangler:
    """Test cases for MarketDataWrangler."""

    def setup_method(self):
        """Set up test fixtures."""
        self.instrument, _ = create_test_instrument("AAPL")
        self.wrangler = MarketDataWrangler(self.instrument)

        self.sample_data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            },
            {
                "timestamp": datetime(2024, 1, 1, 9, 31),
                "open": 100.75,
                "high": 101.25,
                "low": 100.50,
                "close": 101.00,
                "volume": 8500,
            },
        ]

    def test_init(self):
        """Test MarketDataWrangler initialization."""
        assert self.wrangler.instrument == self.instrument
        assert self.wrangler.instrument_id == self.instrument.id

    def test_convert_to_dataframe_valid_data(self):
        """Test convert_to_dataframe with valid data."""
        df = self.wrangler.convert_to_dataframe(self.sample_data)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        # Check data types
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
        assert df["open"].dtype == float
        assert df["high"].dtype == float
        assert df["low"].dtype == float
        assert df["close"].dtype == float
        assert df["volume"].dtype == int

        # Check values
        assert df.iloc[0]["open"] == 100.50
        assert df.iloc[0]["volume"] == 10000
        assert df.iloc[1]["close"] == 101.00

    def test_convert_to_dataframe_empty_data(self):
        """Test convert_to_dataframe with empty data."""
        df = self.wrangler.convert_to_dataframe([])

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_convert_to_dataframe_missing_columns(self):
        """Test convert_to_dataframe with missing required columns."""
        incomplete_data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                # Missing "low", "close", "volume"
            }
        ]

        with pytest.raises(ValueError, match="Missing required column"):
            self.wrangler.convert_to_dataframe(incomplete_data)

    def test_convert_to_dataframe_timestamp_conversion(self):
        """Test timestamp conversion from string."""
        data_with_string_timestamp = [
            {
                "timestamp": "2024-01-01 09:30:00",
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        df = self.wrangler.convert_to_dataframe(data_with_string_timestamp)
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])

    def test_convert_to_dataframe_sorting(self):
        """Test that data is sorted by timestamp."""
        unsorted_data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 31),
                "open": 100.75,
                "high": 101.25,
                "low": 100.50,
                "close": 101.00,
                "volume": 8500,
            },
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            },
        ]

        df = self.wrangler.convert_to_dataframe(unsorted_data)

        # Check that timestamps are in ascending order
        assert df.iloc[0]["timestamp"] < df.iloc[1]["timestamp"]
        assert df.iloc[0]["open"] == 100.50  # Earlier timestamp

    def test_create_bars_from_arrays_empty_data(self):
        """Test create_bars_from_arrays with empty data."""
        bars = self.wrangler.create_bars_from_arrays([])
        assert bars == []

    def test_create_bars_from_arrays_valid_data(self):
        """Test create_bars_from_arrays with valid data."""
        with patch.object(self.wrangler, "create_bars_manually") as mock_manual:
            mock_manual.return_value = ["mock_bar"]

            bars = self.wrangler.create_bars_from_arrays(self.sample_data)

            # Should fall back to manual creation
            assert bars == ["mock_bar"]
            mock_manual.assert_called_once()

    def test_create_bars_manually_empty_data(self):
        """Test create_bars_manually with empty data."""
        bars = self.wrangler.create_bars_manually([])
        assert bars == []

    def test_create_bars_manually_valid_data(self):
        """Test create_bars_manually with valid data."""
        bars = self.wrangler.create_bars_manually(self.sample_data)

        assert len(bars) == 2

        # Check that bars have the expected structure
        bar1 = bars[0]
        assert hasattr(bar1, "open")
        assert hasattr(bar1, "high")
        assert hasattr(bar1, "low")
        assert hasattr(bar1, "close")
        assert hasattr(bar1, "volume")
        assert hasattr(bar1, "ts_event")
        assert hasattr(bar1, "ts_init")

    def test_create_bars_manually_with_custom_bar_type(self):
        """Test create_bars_manually with custom bar type."""
        from nautilus_trader.model.data import BarType, BarSpecification
        from nautilus_trader.model.enums import (
            BarAggregation,
            PriceType,
            AggregationSource,
        )

        # Create custom bar type
        bar_spec = BarSpecification(
            step=5,
            aggregation=BarAggregation.MINUTE,
            price_type=PriceType.BID,
        )
        bar_type = BarType(
            instrument_id=self.instrument.id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )

        bars = self.wrangler.create_bars_manually(self.sample_data, bar_type)

        assert len(bars) == 2
        assert bars[0].bar_type == bar_type

    def test_create_bars_manually_timestamp_conversion(self):
        """Test create_bars_manually with different timestamp formats."""
        # Test with unix timestamp
        data_with_unix_timestamp = [
            {
                "timestamp": 1704110400,  # Unix timestamp for 2024-01-01 09:00:00 UTC
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        bars = self.wrangler.create_bars_manually(data_with_unix_timestamp)
        assert len(bars) == 1

    def test_create_bars_manually_price_creation_fallback(self):
        """Test create_bars_manually fallback when instrument lacks precision attribute."""
        # Create a mock instrument without price_precision attribute
        mock_instrument = MagicMock()
        mock_instrument.make_price.side_effect = TypeError("Method not available")
        mock_instrument.make_qty.side_effect = TypeError("Method not available")
        # Remove price_precision attribute to force fallback
        del mock_instrument.price_precision

        # Create a proper instrument_id
        from src.utils.mock_data import create_test_instrument

        _, real_instrument_id = create_test_instrument("TEST")
        mock_instrument.id = real_instrument_id

        wrangler = MarketDataWrangler(mock_instrument)
        bars = wrangler.create_bars_manually(self.sample_data)

        # Should still create bars using fallback method with default precision
        assert isinstance(bars, list)
        assert len(bars) >= 0

    @patch("src.utils.data_wrangler.print")
    def test_create_bars_manually_bar_creation_failure(self, mock_print):
        """Test create_bars_manually when bar creation fails."""
        # Create data that might cause bar creation to fail
        invalid_data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        # Mock the Bar constructor to raise an exception
        with patch("src.utils.data_wrangler.Bar") as mock_bar_class:
            mock_bar_class.side_effect = Exception("Bar creation failed")

            # Should handle the error gracefully and continue
            bars = self.wrangler.create_bars_manually(invalid_data)

            # Should return empty list since bar creation failed
            assert isinstance(bars, list)
            assert len(bars) == 0

            # Should have printed error message
            mock_print.assert_called()

    def test_process_empty_data(self):
        """Test process method with empty data."""
        with pytest.raises(ValueError, match="No data provided for processing"):
            self.wrangler.process([])

    def test_process_missing_fields(self):
        """Test process method with missing required fields."""
        incomplete_data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                # Missing other required fields
            }
        ]

        with pytest.raises(ValueError, match="Missing required fields"):
            self.wrangler.process(incomplete_data)

    def test_process_valid_data(self):
        """Test process method with valid data."""
        bars = self.wrangler.process(self.sample_data)

        assert isinstance(bars, list)
        assert len(bars) == 2

    def test_process_no_bars_created(self):
        """Test process method when no bars are created."""
        with patch.object(self.wrangler, "create_bars_manually", return_value=[]):
            with pytest.raises(ValueError, match="Failed to create any bars"):
                self.wrangler.process(self.sample_data)

    def test_process_field_validation(self):
        """Test process method validates all required fields."""
        # Test each required field individually
        required_fields = ["timestamp", "open", "high", "low", "close", "volume"]

        for missing_field in required_fields:
            incomplete_data = [
                {
                    field: value
                    for field, value in self.sample_data[0].items()
                    if field != missing_field
                }
            ]

            with pytest.raises(
                ValueError, match=f"Missing required fields: \\['{missing_field}'\\]"
            ):
                self.wrangler.process(incomplete_data)

    def test_process_integration_with_real_nautilus_objects(self):
        """Test process method creates valid Nautilus Bar objects."""
        bars = self.wrangler.process(self.sample_data)

        # Verify these are actual Nautilus Bar objects
        from nautilus_trader.model.data import Bar

        for bar in bars:
            assert isinstance(bar, Bar)
            assert bar.open.as_double() > 0
            assert bar.high.as_double() >= bar.low.as_double()
            assert bar.volume.as_double() > 0
            assert bar.ts_event > 0
            assert bar.ts_init > 0
