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


def _fresh_instrument_row() -> MagicMock:
    """Return a mock CatalogInstrument in a fresh/new state.

    Story 1-7's classifier compares ``date_range_end`` and
    ``bar_count_<tf>`` using real operators, so the mock must carry
    concrete values (not auto-attrs that raise ``TypeError`` on ``<=``).
    """
    instrument = MagicMock()
    instrument.date_range_end = None
    instrument.date_range_start = None
    instrument.bar_count_daily = 0
    instrument.bar_count_hourly = 0
    instrument.bar_count_minute = 0
    return instrument


@pytest.fixture()
def mock_metadata_service():
    mock = MagicMock()
    # Fresh instrument rows by default so the classifier picks "new"
    # and the full import path runs. Story-1-7 tests override per-ticker.
    mock.get_instrument_sync.side_effect = lambda _c, _t: _fresh_instrument_row()
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


# ---------------------------------------------------------------------------
# Story 1-7 — idempotent re-run end-to-end
# ---------------------------------------------------------------------------


def _multi_row_csv(last_day: str) -> str:
    """Two-row FirstRate daily CSV ending on ``last_day``."""
    return f"2000-01-01,100,105,95,102,1000\n{last_day},110,115,105,112,1100\n"


@pytest.fixture()
def idempotent_source_dir(tmp_path: Path) -> Path:
    """Source dir with AAPL + SPY daily CSVs ending 2025-01-15."""
    a_dir = tmp_path / "A"
    a_dir.mkdir()
    (a_dir / "AAPL.txt").write_text(_multi_row_csv("2025-01-15"))

    s_dir = tmp_path / "S"
    s_dir.mkdir()
    (s_dir / "SPY.txt").write_text(_multi_row_csv("2025-01-15"))

    return tmp_path


@pytest.fixture()
def stateful_metadata_service():
    """Metadata service that persists upserts into a per-(catalog, ticker) dict.

    Mirrors the real sync repo's contract just enough for Story 1-7:
    - ``get_instrument_sync`` returns the stored row or a fresh one.
    - ``upsert_instrument_sync`` stores the mutated row back.

    Tests can reach into ``.storage`` to simulate partial/orphan states
    between calls to ``import_directory``.
    """
    from datetime import datetime, timezone

    mock = MagicMock()
    storage: dict[tuple[str, str], MagicMock] = {}

    def _get(catalog: str, ticker: str):
        return storage.get((catalog, ticker))

    def _upsert(instrument: MagicMock):
        key = (instrument.catalog_name, instrument.ticker)
        storage[key] = instrument
        return instrument

    # Pre-populate fresh rows for AAPL / SPY so the first run behaves like
    # a post-profile-load state.
    def _seed(catalog: str, ticker: str) -> None:
        row = MagicMock()
        row.catalog_name = catalog
        row.ticker = ticker
        row.date_range_start = None
        row.date_range_end = None
        row.bar_count_daily = 0
        row.bar_count_hourly = 0
        row.bar_count_minute = 0
        storage[(catalog, ticker)] = row

    _seed(CATALOG_NAME, "AAPL")
    _seed(CATALOG_NAME, "SPY")

    mock.get_instrument_sync.side_effect = _get
    mock.upsert_instrument_sync.side_effect = _upsert
    mock.storage = storage

    # Export a quick constant so tests can build datetimes alongside.
    mock._tz = timezone.utc
    mock._datetime = datetime
    return mock


def _install_parser_and_readback(
    mock_catalog_manager,
    bars_by_ticker,
):
    """Patch ``get_parser`` and catalog read-back to mirror a real import.

    Returns the patch context manager so callers can use it inside
    a ``with`` block.
    """
    mock_catalog = mock_catalog_manager.resolve_catalog.return_value

    def _bars_readback(**_kwargs):
        if mock_catalog.write_data.call_args:
            return mock_catalog.write_data.call_args[0][0]
        return []

    mock_catalog.bars.side_effect = _bars_readback

    mock_parser = MagicMock()

    def _parse_file(file_path, _iid, _bt):
        ticker = file_path.stem
        return bars_by_ticker[ticker]

    mock_parser.parse_file.side_effect = _parse_file

    cm = patch("src.services.firstrate.import_service.get_parser")
    return cm, mock_parser


def _force_bar_end_to_2025_01_15(bars_by_ticker: dict) -> None:
    """Align the last bar's ``ts_init`` to 2025-01-15 UTC for every ticker.

    ``_make_bars`` produces 2020-dated bars, but our idempotent source
    CSVs end on 2025-01-15. After the first run, ``_upsert_metadata``
    stores ``bars[-1].ts_init`` as the metadata end. Forcing that value
    here lines metadata and source up so the classifier can actually
    pick ``"skipped"`` on a re-run.
    """
    from datetime import datetime, timezone

    end_ts_ns = int(datetime(2025, 1, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    for ticker in bars_by_ticker:
        bars_by_ticker[ticker][-1].ts_init = end_ts_ns


@pytest.mark.component
class TestIdempotentRerun:
    """Story 1-7: a second run over the same source skips complete tickers."""

    def test_second_run_skips_complete_tickers(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        _force_bar_end_to_2025_01_15(bars_by_ticker)
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )

            first = service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

            assert sorted(r.ticker for r in first) == ["AAPL", "SPY"]
            assert {r.outcome for r in first} == {"new"}
            first_write_count = mock_catalog.write_data.call_count
            assert first_write_count == 2  # AAPL + SPY

            # Second run — metadata now matches source last date exactly.
            second = service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert sorted(r.ticker for r in second) == ["AAPL", "SPY"]
        assert {r.outcome for r in second} == {"skipped"}
        assert {r.status for r in second} == {"skipped"}
        assert all(r.row_count == 0 for r in second)

        # catalog.write_data was NOT called again during the second run.
        assert mock_catalog.write_data.call_count == first_write_count

    def test_partial_metadata_triggers_reimport(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        _force_bar_end_to_2025_01_15(bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )

            # First run — fully imports AAPL+SPY, metadata rows now hold
            # a date_range_end derived from bars[-1].ts_init.
            service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

            # Simulate a truncated/partial prior import: rewind AAPL's
            # date_range_end to 2024-12-31 while keeping bar_count > 0.
            aapl_row = stateful_metadata_service.storage[(CATALOG_NAME, "AAPL")]
            aapl_row.date_range_end = stateful_metadata_service._datetime(
                2024, 12, 31, tzinfo=stateful_metadata_service._tz
            )
            aapl_row.bar_count_daily = 5

            # SPY stays "complete" and must be skipped on re-run; its
            # metadata end still reflects the bars[-1].ts_init from run 1.
            baseline_writes = (
                mock_catalog_manager.resolve_catalog.return_value.write_data.call_count
            )

            second = service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        by_ticker = {r.ticker: r for r in second}
        assert by_ticker["AAPL"].outcome == "reimported"
        assert by_ticker["AAPL"].status == "success"
        # AAPL was rewritten (one extra write_data call since baseline).
        assert (
            mock_catalog_manager.resolve_catalog.return_value.write_data.call_count
            == baseline_writes + 1
        )
        # AAPL's metadata row ended up refreshed to the post-import end.
        refreshed = stateful_metadata_service.storage[(CATALOG_NAME, "AAPL")]
        assert refreshed.date_range_end != stateful_metadata_service._datetime(
            2024, 12, 31, tzinfo=stateful_metadata_service._tz
        )

    def test_orphan_metadata_triggers_new_import(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        _force_bar_end_to_2025_01_15(bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
            )

            # First run — AAPL+SPY both import fresh.
            service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

            # Simulate an orphan (crash after write_data, before upsert):
            # zero-out AAPL's metadata state.
            aapl_row = stateful_metadata_service.storage[(CATALOG_NAME, "AAPL")]
            aapl_row.date_range_end = None
            aapl_row.bar_count_daily = 0

            baseline_writes = (
                mock_catalog_manager.resolve_catalog.return_value.write_data.call_count
            )

            second = service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        by_ticker = {r.ticker: r for r in second}
        assert by_ticker["AAPL"].outcome == "new"
        assert by_ticker["AAPL"].status == "success"
        # AAPL's orphaned Parquet is overwritten (one more write_data call).
        assert (
            mock_catalog_manager.resolve_catalog.return_value.write_data.call_count
            == baseline_writes + 1
        )
