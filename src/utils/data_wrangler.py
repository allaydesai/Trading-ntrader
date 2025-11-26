"""Data wrangler for converting market data to Nautilus Trader format."""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity


class MarketDataWrangler:
    """
    Wrangle market data for use with Nautilus Trader.

    Converts raw market data to properly formatted Nautilus objects
    following the framework's best practices.
    """

    def __init__(self, instrument: Instrument):
        """
        Initialize the data wrangler.

        Args:
            instrument: The Nautilus Trader instrument object
        """
        self.instrument = instrument
        self.instrument_id = instrument.id

    def convert_to_dataframe(self, data: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Convert market data to pandas DataFrame with proper structure.

        Args:
            data: List of market data dictionaries

        Returns:
            DataFrame formatted for Nautilus Trader consumption
        """
        if not data:
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(data)

        # Ensure timestamp is datetime
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Ensure proper column names and types
        required_columns = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        # Convert price columns to float
        price_columns = ["open", "high", "low", "close"]
        for col in price_columns:
            df[col] = df[col].astype(float)

        # Convert volume to int
        df["volume"] = df["volume"].astype(int)

        # Sort by timestamp
        df = df.sort_values("timestamp").reset_index(drop=True)

        return df

    def create_bars_from_arrays(self, data: List[Dict[str, Any]]) -> List[Bar]:
        """
        Create Nautilus Bar objects using the array conversion method.

        Args:
            data: List of market data dictionaries

        Returns:
            List of Nautilus Bar objects
        """
        if not data:
            return []

        # Convert to arrays
        open_prices = []
        high_prices = []
        low_prices = []
        close_prices = []
        volumes = []
        timestamps_event = []
        timestamps_init = []

        for record in data:
            # Convert prices using instrument precision
            open_prices.append(float(record["open"]))
            high_prices.append(float(record["high"]))
            low_prices.append(float(record["low"]))
            close_prices.append(float(record["close"]))
            volumes.append(int(record["volume"]))

            # Convert timestamp to nanoseconds
            if isinstance(record["timestamp"], datetime):
                ts_ns = int(record["timestamp"].timestamp() * 1_000_000_000)
            else:
                # Assume it's already a timestamp
                ts_ns = int(record["timestamp"] * 1_000_000_000)

            timestamps_event.append(ts_ns)
            timestamps_init.append(ts_ns)

        # Create bar type for 1-minute bars
        bar_spec = BarSpecification(
            step=1,
            aggregation=BarAggregation.MINUTE,
            price_type=PriceType.MID,
        )
        bar_type = BarType(
            instrument_id=self.instrument_id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )

        # Try to use the from_raw_arrays_to_list method if available
        try:
            # Check if the method exists
            if hasattr(Bar, "from_raw_arrays_to_list"):
                print("  Trying from_raw_arrays_to_list method")
                bars = Bar.from_raw_arrays_to_list(
                    bar_type=bar_type,
                    open=open_prices,
                    high=high_prices,
                    low=low_prices,
                    close=close_prices,
                    volume=volumes,
                    ts_event=timestamps_event,
                    ts_init=timestamps_init,
                )
                return bars
            else:
                print("  from_raw_arrays_to_list method not available")
        except Exception as e:
            # If the method doesn't work, fall back to manual creation
            print(f"  Failed to use from_raw_arrays_to_list: {e}")

        # Fallback: Create bars manually using proper Price/Quantity objects
        print("  Falling back to manual bar creation")
        return self.create_bars_manually(data, bar_type)

    def create_bars_manually(
        self, data: List[Dict[str, Any]], bar_type: Optional[BarType] = None
    ) -> List[Bar]:
        """
        Create Nautilus Bar objects manually using proper Price/Quantity creation.

        Args:
            data: List of market data dictionaries
            bar_type: Optional bar type, will create default if None

        Returns:
            List of Nautilus Bar objects
        """
        if not data:
            return []

        # Create default bar type if not provided
        if bar_type is None:
            bar_spec = BarSpecification(
                step=1,
                aggregation=BarAggregation.MINUTE,
                price_type=PriceType.MID,
            )
            bar_type = BarType(
                instrument_id=self.instrument_id,
                bar_spec=bar_spec,
                aggregation_source=AggregationSource.EXTERNAL,
            )

        bars = []

        for record in data:
            # Convert timestamp to nanoseconds
            if isinstance(record["timestamp"], datetime):
                ts_event = int(record["timestamp"].timestamp() * 1_000_000_000)
            else:
                ts_event = int(record["timestamp"] * 1_000_000_000)
            ts_init = ts_event

            # Use instrument methods to create proper Price and Quantity objects
            try:
                # Try using instrument methods first
                open_price = self.instrument.make_price(float(record["open"]))
                high_price = self.instrument.make_price(float(record["high"]))
                low_price = self.instrument.make_price(float(record["low"]))
                close_price = self.instrument.make_price(float(record["close"]))
                volume = self.instrument.make_qty(int(record["volume"]))
            except (AttributeError, TypeError, Exception):
                # Fallback to direct Price/Quantity creation
                precision = getattr(self.instrument, "price_precision", 5)
                open_price = Price.from_str(f"{float(record['open']):.{precision}f}")
                high_price = Price.from_str(f"{float(record['high']):.{precision}f}")
                low_price = Price.from_str(f"{float(record['low']):.{precision}f}")
                close_price = Price.from_str(f"{float(record['close']):.{precision}f}")
                volume = Quantity.from_int(int(record["volume"]))

            # Create Bar object
            try:
                bar = Bar(
                    bar_type=bar_type,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    ts_event=ts_event,
                    ts_init=ts_init,
                )
                bars.append(bar)
            except Exception as e:
                # Log the error and continue with next bar
                print(f"Failed to create bar for timestamp {record['timestamp']}: {e}")
                continue

        return bars

    def process(self, data: List[Dict[str, Any]]) -> List[Bar]:
        """
        Main processing method to convert data to Nautilus Bars.

        Args:
            data: List of market data dictionaries

        Returns:
            List of Nautilus Bar objects

        Raises:
            ValueError: If data is invalid or conversion fails
        """
        if not data:
            raise ValueError("No data provided for processing")

        # Validate data structure
        required_fields = ["timestamp", "open", "high", "low", "close", "volume"]
        first_record = data[0]
        missing_fields = [field for field in required_fields if field not in first_record]
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        # Use manual creation method which has proven to work reliably
        bars = self.create_bars_manually(data)
        if bars:
            return bars
        else:
            raise ValueError("Failed to create any bars from the provided data")
