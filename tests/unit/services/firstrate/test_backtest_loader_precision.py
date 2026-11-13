"""Price-precision reconciliation for the default-catalog backtest path.

The default (no ``--catalog``) path takes the instrument stored in the catalog, which the
importer wrote with ``price_precision=2``, while FirstRate bars carry 4 decimals. Nautilus
refuses the mix (``invalid bar.open.precision=4 did not match
self.instrument.price_precision=2``). The named-catalog loader avoids that by inferring the
precision from the bars; these tests pin that the default path now does the same, through the
same ``build_equity`` logic.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import CurrencyPair, Equity
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from rich.console import Console

from src.services.firstrate.backtest_loader import (
    build_equity,
    reconcile_equity_price_precision,
)

BAR_TYPE = BarType.from_str("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL")


def _bars(price: str, count: int = 3) -> list[Bar]:
    """Build real Nautilus bars whose OHLC all carry the precision of ``price``."""
    p = Price.from_str(price)
    ts = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return [
        Bar(BAR_TYPE, p, p, p, p, Quantity.from_int(1000), ts + i, ts + i) for i in range(count)
    ]


def _equity(precision: int) -> Equity:
    """An AAPL.NASDAQ equity at the given price precision."""
    return build_equity("AAPL.NASDAQ", "AAPL", bars=_bars("187." + "1" * precision))


@pytest.mark.unit
class TestReconcileEquityPricePrecision:
    """reconcile_equity_price_precision aligns an Equity with its bars' precision."""

    def test_precision_2_instrument_with_precision_4_bars_is_rebuilt(self):
        """The reported bug: catalog instrument is 2dp, FirstRate bars are 4dp."""
        stored = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
        assert stored.price_precision == 2

        result = reconcile_equity_price_precision(stored, _bars("187.1500"))

        assert isinstance(result, Equity)
        assert result.price_precision == 4
        assert result.price_increment == Price.from_str("0.0001")
        assert str(result.id) == "AAPL.NASDAQ"

    def test_precision_4_instrument_with_precision_2_bars_is_rebuilt(self):
        """The inference is symmetric — it follows the bars, not a fixed direction."""
        result = reconcile_equity_price_precision(_equity(4), _bars("187.15"))

        assert result.price_precision == 2
        assert result.price_increment == Price.from_str("0.01")

    def test_matching_precision_4_returns_same_instrument(self):
        instrument = _equity(4)
        assert reconcile_equity_price_precision(instrument, _bars("187.1500")) is instrument

    def test_matching_precision_2_returns_same_instrument(self):
        instrument = _equity(2)
        assert reconcile_equity_price_precision(instrument, _bars("187.15")) is instrument

    def test_matches_named_catalog_construction(self):
        """Default and named paths must end up with the same instrument for the same bars."""
        bars = _bars("187.1500")
        default_path = reconcile_equity_price_precision(_equity(2), bars)
        named_path = build_equity("AAPL.NASDAQ", "AAPL", bars=bars)

        assert default_path == named_path
        assert default_path.price_precision == named_path.price_precision
        assert default_path.price_increment == named_path.price_increment

    def test_empty_bars_returns_same_instrument(self):
        instrument = _equity(2)
        assert reconcile_equity_price_precision(instrument, []) is instrument

    def test_non_equity_instrument_is_untouched(self):
        pair = TestInstrumentProvider.default_fx_ccy("EUR/USD")
        assert isinstance(pair, CurrencyPair)
        assert reconcile_equity_price_precision(pair, _bars("1.10000")) is pair

    def test_non_instrument_double_is_untouched(self):
        double = MagicMock()
        assert reconcile_equity_price_precision(double, _bars("187.1500")) is double

    def test_none_instrument_is_untouched(self):
        assert reconcile_equity_price_precision(None, _bars("187.1500")) is None


@pytest.mark.unit
class TestDefaultCatalogPathAppliesPrecision:
    """_load_catalog_data (the no --catalog path) returns an instrument matching its bars."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("bar_price", "expected_precision"),
        [("187.15", 2), ("187.1500", 4)],
    )
    async def test_instrument_precision_follows_bars(self, bar_price, expected_precision):
        from src.cli.commands._backtest_helpers import _load_catalog_data

        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        end = datetime(2024, 6, 28, tzinfo=timezone.utc)
        bars = _bars(bar_price)

        availability = MagicMock()
        availability.covers_range.return_value = True
        availability.file_count = 1
        availability.total_rows = 3

        async def fetch_or_load(*args, **kwargs):
            return bars

        service = MagicMock()
        service.get_availability.return_value = availability
        service.fetch_or_load = fetch_or_load
        # The catalog stores the instrument at the importer's default of 2 decimals.
        service.load_instrument.return_value = TestInstrumentProvider.equity(
            symbol="AAPL", venue="NASDAQ"
        )

        result = await _load_catalog_data(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-DAY-LAST",
            start=start,
            end=end,
            console=Console(quiet=True),
            catalog_service=service,
        )

        assert result.bars == bars
        assert result.instrument.price_precision == expected_precision
        assert str(result.instrument.id) == "AAPL.NASDAQ"
