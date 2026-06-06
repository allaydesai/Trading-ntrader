"""Unit tests for base parser ABC and parser registry."""

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass
from src.services.firstrate.parsers.base import (
    _PARSER_REGISTRY,
    BaseParser,
    RawBarData,
    get_parser,
    register_parser,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the parser registry before and after each test."""
    saved = dict(_PARSER_REGISTRY)
    _PARSER_REGISTRY.clear()
    yield
    _PARSER_REGISTRY.clear()
    _PARSER_REGISTRY.update(saved)


# --- BaseParser ABC Tests ---


@pytest.mark.unit
class TestBaseParserABC:
    """Tests for BaseParser abstract base class."""

    def test_cannot_instantiate_directly(self):
        """BaseParser is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseParser()

    def test_subclass_must_implement_parse_file(self):
        """Subclass missing parse_file raises TypeError."""

        class Incomplete(BaseParser):
            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        with pytest.raises(TypeError):
            Incomplete()

    def test_subclass_must_implement_map_instrument_id(self):
        """Subclass missing map_instrument_id raises TypeError."""

        class Incomplete(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_can_be_instantiated(self):
        """A fully-implemented subclass can be instantiated."""

        class ConcreteParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        parser = ConcreteParser()
        assert parser is not None

    def test_validate_bars_is_concrete_on_base(self):
        """validate_bars is a concrete method on the base class."""

        class ConcreteParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        parser = ConcreteParser()
        assert hasattr(parser, "validate_bars")
        assert callable(parser.validate_bars)


# --- Registry Tests ---


@pytest.mark.unit
class TestParserRegistry:
    """Tests for @register_parser decorator and get_parser()."""

    def test_register_and_retrieve_parser(self):
        """Registered parser can be retrieved by asset class."""

        @register_parser(asset_class=AssetClass.ETF)
        class DummyETFParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        parser = get_parser(AssetClass.ETF)
        assert isinstance(parser, DummyETFParser)

    def test_get_unregistered_asset_class_raises(self):
        """Requesting parser for unregistered asset class raises ValueError."""
        with pytest.raises(ValueError, match="No parser registered for"):
            get_parser(AssetClass.FX)

    def test_duplicate_registration_overwrites(self):
        """Registering same asset class twice overwrites the first."""

        @register_parser(asset_class=AssetClass.ETF)
        class First(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        @register_parser(asset_class=AssetClass.ETF)
        class Second(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        parser = get_parser(AssetClass.ETF)
        assert isinstance(parser, Second)

    def test_register_multiple_asset_classes(self):
        """Multiple asset classes can each have their own parser."""

        @register_parser(asset_class=AssetClass.ETF)
        class ETFParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        @register_parser(asset_class=AssetClass.FX)
        class FXParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.IDEALPRO")

        assert isinstance(get_parser(AssetClass.ETF), ETFParser)
        assert isinstance(get_parser(AssetClass.FX), FXParser)

    def test_get_parser_returns_new_instance_each_call(self):
        """get_parser returns a fresh instance each time."""

        @register_parser(asset_class=AssetClass.ETF)
        class DummyParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        p1 = get_parser(AssetClass.ETF)
        p2 = get_parser(AssetClass.ETF)
        assert p1 is not p2


# --- validate_bars Tests ---


@pytest.mark.unit
class TestValidateBars:
    """Tests for the shared validate_bars() method on BaseParser.

    validate_bars operates on RawBarData (pre-construction) because
    Nautilus Bar enforces OHLC correctness at construction time.
    """

    @pytest.fixture()
    def parser(self):
        """Create a concrete parser for testing validate_bars."""

        class ConcreteParser(BaseParser):
            def parse_file(self, file_path, instrument_id, bar_type):
                return []

            def map_instrument_id(self, ticker, bar_type_spec):
                return InstrumentId.from_str(f"{ticker}.ARCA")

        return ConcreteParser()

    def _raw(
        self,
        open_: str = "100.00",
        high: str = "105.00",
        low: str = "99.00",
        close: str = "103.00",
        volume: str = "1000",
        timestamp: str = "2020-01-01 09:00:00",
    ) -> RawBarData:
        return RawBarData(
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    def test_valid_bars_pass(self, parser):
        """All-valid rows produce a valid ValidationResult."""
        rows = [
            self._raw("100.00", "105.00", "99.00", "103.00", "1000"),
            self._raw("103.00", "108.00", "102.00", "107.00", "2000"),
        ]
        result = parser.validate_bars(rows)
        assert result.valid is True
        assert result.row_count == 2
        assert result.invalid_rows == 0
        assert result.errors == []

    def test_high_less_than_low_flagged(self, parser):
        """Rows where high < low are flagged as invalid."""
        rows = [self._raw(high="98.00", low="99.00")]
        result = parser.validate_bars(rows)
        assert result.valid is False
        assert result.invalid_rows == 1
        assert any("high" in e.lower() and "low" in e.lower() for e in result.errors)

    def test_negative_volume_flagged(self, parser):
        """Rows with volume < 0 are flagged."""
        rows = [self._raw(volume="-100")]
        result = parser.validate_bars(rows)
        assert result.valid is False
        assert result.invalid_rows == 1
        assert any("volume" in e.lower() for e in result.errors)

    def test_zero_volume_valid(self, parser):
        """Volume = 0 is valid."""
        rows = [self._raw(volume="0")]
        result = parser.validate_bars(rows)
        assert result.valid is True

    def test_negative_prices_flagged(self, parser):
        """Negative OHLC prices (ARKD/IDAT pattern) are flagged."""
        rows = [self._raw(open_="-5.00", high="-4.00", low="-6.00", close="-5.50")]
        result = parser.validate_bars(rows)
        assert result.valid is False
        assert result.invalid_rows == 1

    def test_mixed_valid_invalid(self, parser):
        """Mix of valid and invalid rows reports correct counts."""
        rows = [
            self._raw("100.00", "105.00", "99.00", "103.00", "1000"),
            self._raw(high="98.00", low="99.00"),  # invalid
            self._raw("100.00", "110.00", "95.00", "108.00", "2000"),
        ]
        result = parser.validate_bars(rows)
        assert result.valid is False
        assert result.row_count == 3
        assert result.invalid_rows == 1

    def test_empty_list_valid(self, parser):
        """Empty list is valid."""
        result = parser.validate_bars([])
        assert result.valid is True
        assert result.row_count == 0
        assert result.invalid_rows == 0

    def test_error_messages_include_row_detail(self, parser):
        """Error messages include specific row information."""
        rows = [self._raw(high="98.00", low="99.00")]
        result = parser.validate_bars(rows)
        assert len(result.errors) >= 1
        assert any("row" in e.lower() for e in result.errors)

    def test_all_rows_invalid(self, parser):
        """All-invalid rows still return correct counts."""
        rows = [
            self._raw(high="10.00", low="20.00"),
            self._raw(volume="-50"),
        ]
        result = parser.validate_bars(rows)
        assert result.valid is False
        assert result.row_count == 2
        assert result.invalid_rows == 2
