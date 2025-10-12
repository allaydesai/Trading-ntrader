"""Nautilus Trader data format converter."""

from typing import List, Dict, Any

from nautilus_trader.model.data import Bar, BarType, BarSpecification
from nautilus_trader.model.enums import BarAggregation, PriceType, AggregationSource
from nautilus_trader.model.objects import Price, Quantity


class NautilusConverter:
    """Converts market data to Nautilus Trader format."""

    def convert_to_nautilus_bars(
        self, data: List[Dict[str, Any]], instrument_id, instrument=None
    ) -> List:
        """
        Convert market data to Nautilus Trader Bar objects.

        Args:
            data: List of market data dictionaries
            instrument_id: Nautilus InstrumentId object
            instrument: Optional Nautilus Instrument object for proper conversion

        Returns:
            List of Nautilus Bar objects

        Raises:
            ValueError: If data conversion fails
            ImportError: If required modules are not available
        """
        if not data:
            return []

        try:
            # Import the data wrangler
            from src.utils.data_wrangler import MarketDataWrangler

            # If instrument is not provided, create a basic one
            if instrument is None:
                from src.utils.mock_data import create_test_instrument

                # Extract symbol from instrument_id
                symbol = str(instrument_id).split(".")[0]
                instrument, _ = create_test_instrument(symbol)

            # Create wrangler and process data
            wrangler = MarketDataWrangler(instrument)
            bars = wrangler.process(data)

            if not bars:
                raise ValueError("No bars were created from the provided data")

            # Successfully converted data to bars
            return bars

        except ImportError as e:
            print(f"Failed to import data wrangler: {e}")
            # Fallback to original implementation
            return self._convert_to_nautilus_bars_fallback(data, instrument_id)

        except Exception as e:
            print(f"Error converting data to Nautilus bars: {e}")
            # Fallback to original implementation
            return self._convert_to_nautilus_bars_fallback(data, instrument_id)

    def _convert_to_nautilus_bars_fallback(
        self, data: List[Dict[str, Any]], instrument_id
    ) -> List:
        """
        Fallback method for converting market data to Nautilus Trader Bar objects.

        Args:
            data: List of market data dictionaries
            instrument_id: Nautilus InstrumentId object

        Returns:
            List of Nautilus Bar objects
        """
        bars = []

        # Create bar type specification for 1-minute bars
        bar_spec = BarSpecification(
            step=1,
            aggregation=BarAggregation.MINUTE,
            price_type=PriceType.MID,
        )
        bar_type = BarType(
            instrument_id=instrument_id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )

        for record in data:
            try:
                # Convert timestamp to nanoseconds since Unix epoch
                ts_event = int(record["timestamp"].timestamp() * 1_000_000_000)
                ts_init = ts_event

                # Create price objects (Nautilus uses 5 decimal places precision)
                open_price = Price.from_str(f"{record['open']:.5f}")
                high_price = Price.from_str(f"{record['high']:.5f}")
                low_price = Price.from_str(f"{record['low']:.5f}")
                close_price = Price.from_str(f"{record['close']:.5f}")

                # Create volume quantity
                volume = Quantity.from_int(int(record["volume"]))

                # Create Bar object
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
                print(f"Failed to create bar for record {record}: {e}")
                continue

        # Return the created bars
        return bars

    def convert_to_nautilus_format(
        self, data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert market data to format suitable for Nautilus Trader.

        Args:
            data: List of market data dictionaries

        Returns:
            List of data in Nautilus-compatible format

        Note:
            This is a placeholder implementation. Full Nautilus integration
            will be implemented when we have the backtest runner integration.
        """
        nautilus_data = []

        for record in data:
            # Convert to Nautilus Bar format (simplified)
            nautilus_record = {
                "instrument_id": f"{record['symbol']}.SIM",
                "bar_type": f"{record['symbol']}.SIM-1-MINUTE-MID-EXTERNAL",
                "ts_event": int(record["timestamp"].timestamp() * 1_000_000_000),
                "ts_init": int(record["timestamp"].timestamp() * 1_000_000_000),
                "open": int(record["open"] * 100000),  # Nautilus uses price precision
                "high": int(record["high"] * 100000),
                "low": int(record["low"] * 100000),
                "close": int(record["close"] * 100000),
                "volume": int(record["volume"]),
            }
            nautilus_data.append(nautilus_record)

        return nautilus_data
