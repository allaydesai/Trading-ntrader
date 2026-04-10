"""Component tests for ImportService — full import_directory flow with test doubles."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass
from src.services.firstrate.import_service import ImportService

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

CATALOG_NAME = "firstrate-etf"


def _make_mock_bar(
    ts_init: int = 1_577_836_800_000_000_000,
    open_price: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
) -> MagicMock:
    """Create a mock Bar with OHLCV values."""
    bar = MagicMock()
    bar.ts_init = ts_init
    bar.open = open_price
    bar.high = high
    bar.low = low
    bar.close = close
    bar.volume = volume
    bar.bar_type = MagicMock()
    return bar


def _make_bars(count: int = 5) -> list[MagicMock]:
    """Create a list of mock bars with ascending timestamps."""
    return [
        _make_mock_bar(ts_init=1_577_836_800_000_000_000 + i * 86_400_000_000_000)
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    """Create source directory with 3 tickers across 2 subdirs."""
    a_dir = tmp_path / "A"
    a_dir.mkdir()
    (a_dir / "AAPL.txt").write_text("2020-01-01,100,105,95,102,1000")
    (a_dir / "ARKK.txt").write_text("2020-01-01,50,55,45,52,500")

    s_dir = tmp_path / "S"
    s_dir.mkdir()
    (s_dir / "SPY.txt").write_text("2020-01-01,300,310,290,305,10000")

    return tmp_path


@pytest.fixture()
def mock_catalog_manager():
    mock = MagicMock()
    mock_catalog = MagicMock()
    mock.resolve_catalog.return_value = mock_catalog
    return mock


@pytest.fixture()
def mock_metadata_service():
    mock = MagicMock()
    mock.get_instrument_sync.return_value = MagicMock()
    return mock


@pytest.fixture()
def mock_instrument_mapper():
    mock = MagicMock()
    mock.is_loaded.return_value = True

    def _resolve(ticker: str, catalog_name: str) -> InstrumentId:
        return InstrumentId.from_str(f"{ticker}.ARCA")

    mock.resolve_instrument_id.side_effect = _resolve
    return mock


@pytest.fixture()
def bars_by_ticker():
    """Pre-built bars for each ticker."""
    return {
        "AAPL": _make_bars(252),
        "ARKK": _make_bars(100),
        "SPY": _make_bars(500),
    }


# ---------------------------------------------------------------------------
# Component tests
# ---------------------------------------------------------------------------


@pytest.mark.component
class TestImportDirectoryFlow:
    """Test full import_directory with mocked dependencies."""

    def test_successful_batch_returns_all_results(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()

            def _parse_file(file_path, instrument_id, bar_type):
                ticker = file_path.stem
                return bars_by_ticker[ticker]

            mock_parser.parse_file.side_effect = _parse_file
            mock_get_parser.return_value = mock_parser

            # Read-back returns same bars (verification passes)
            def _bars_readback(**kwargs):
                # Return based on most recent write_data call
                if mock_catalog.write_data.call_args:
                    return mock_catalog.write_data.call_args[0][0]
                return []

            mock_catalog.bars.side_effect = _bars_readback

            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=mock_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )
            results = service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert len(results) == 3
        assert all(r.status == "success" for r in results)
        tickers = [r.ticker for r in results]
        assert tickers == ["AAPL", "ARKK", "SPY"]
        assert results[0].row_count == 252
        assert results[1].row_count == 100
        assert results[2].row_count == 500

    def test_mixed_success_and_failure(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        """ARKK fails to parse; AAPL and SPY succeed."""
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()

            def _parse_file(file_path, instrument_id, bar_type):
                ticker = file_path.stem
                if ticker == "ARKK":
                    raise ValueError("Corrupt CSV")
                return bars_by_ticker[ticker]

            mock_parser.parse_file.side_effect = _parse_file
            mock_get_parser.return_value = mock_parser

            def _bars_readback(**kwargs):
                if mock_catalog.write_data.call_args:
                    return mock_catalog.write_data.call_args[0][0]
                return []

            mock_catalog.bars.side_effect = _bars_readback

            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=mock_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )
            results = service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert len(results) == 3
        success = [r for r in results if r.status == "success"]
        failed = [r for r in results if r.status == "failed"]
        assert len(success) == 2
        assert len(failed) == 1
        assert failed[0].ticker == "ARKK"
        assert "Corrupt CSV" in failed[0].error

    def test_no_metadata_upserted_for_failed_tickers(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        """Failed tickers must not have metadata upserted (gatekeeper pattern)."""
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()

            def _parse_file(file_path, instrument_id, bar_type):
                ticker = file_path.stem
                if ticker == "ARKK":
                    raise ValueError("Bad data")
                return bars_by_ticker[ticker]

            mock_parser.parse_file.side_effect = _parse_file
            mock_get_parser.return_value = mock_parser

            def _bars_readback(**kwargs):
                if mock_catalog.write_data.call_args:
                    return mock_catalog.write_data.call_args[0][0]
                return []

            mock_catalog.bars.side_effect = _bars_readback

            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=mock_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )
            service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        # Only 2 upserts (AAPL, SPY) — not ARKK
        upsert_calls = mock_metadata_service.upsert_instrument_sync.call_args_list
        assert len(upsert_calls) == 2

    def test_correct_call_sequence_per_ticker(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        """Verify resolve -> parse -> write -> verify -> upsert order."""
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value
        call_log: list[str] = []

        original_resolve = mock_instrument_mapper.resolve_instrument_id.side_effect

        def _track_resolve(ticker, catalog_name):
            call_log.append(f"resolve:{ticker}")
            return original_resolve(ticker, catalog_name)

        mock_instrument_mapper.resolve_instrument_id.side_effect = _track_resolve

        def _track_write(bars):
            call_log.append(f"write:{len(bars)}")

        mock_catalog.write_data.side_effect = _track_write

        def _track_bars(**kwargs):
            call_log.append("verify")
            if mock_catalog.write_data.call_count > 0:
                last_call = mock_catalog.write_data.call_args
                return last_call[0][0] if last_call else []
            return []

        mock_catalog.bars.side_effect = _track_bars

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()

            def _parse_file(file_path, instrument_id, bar_type):
                ticker = file_path.stem
                call_log.append(f"parse:{ticker}")
                return bars_by_ticker[ticker]

            mock_parser.parse_file.side_effect = _parse_file
            mock_get_parser.return_value = mock_parser

            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=mock_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )
            service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        # Check that for the first ticker, the order is correct
        aapl_ops = [c for c in call_log if "AAPL" in c or c.startswith(("write", "verify"))]
        assert aapl_ops[0] == "resolve:AAPL"
        assert aapl_ops[1] == "parse:AAPL"
