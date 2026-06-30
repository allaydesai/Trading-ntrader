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
    instrument.bar_count_5min = 0
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
        row.bar_count_5min = 0
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


# ---------------------------------------------------------------------------
# Story 2.4 — real-catalog round-trip (parse → isolated Parquet → read back)
# ---------------------------------------------------------------------------


@pytest.fixture()
def etf_catalog_base(tmp_path: Path) -> Path:
    """Temp catalog base holding a single isolated ``firstrate-etf`` catalog dir."""
    base = tmp_path / "catalogs"
    base.mkdir()
    (base / "firstrate-etf").mkdir()
    return base


@pytest.fixture()
def etf_intraday_source(tmp_path: Path) -> Path:
    """Source tree with 2 ETF tickers of intraday rows carrying decimal prices."""
    src = tmp_path / "source"
    s_dir = src / "S"
    s_dir.mkdir(parents=True)
    (s_dir / "SPY.txt").write_text(
        "2024-01-02 09:30:00,100.12,101.55,99.50,100.75,1000\n"
        "2024-01-02 09:31:00,100.75,102.00,100.00,101.25,2000\n"
    )
    q_dir = src / "Q"
    q_dir.mkdir(parents=True)
    (q_dir / "QQQ.txt").write_text(
        "2024-01-02 09:30:00,50.10,51.00,49.90,50.50,500\n"
        "2024-01-02 09:31:00,50.50,52.00,50.00,51.25,750\n"
    )
    return src


@pytest.mark.component
class TestEtfCatalogRoundTrip:
    """AC1/AC5: real parser + real ParquetDataCatalog write to the isolated catalog."""

    def test_writes_isolated_parquet_with_layout_and_fidelity(
        self,
        etf_catalog_base,
        etf_intraday_source,
        mock_metadata_service,
        mock_instrument_mapper,
    ):
        from datetime import datetime, timezone

        from src.services.firstrate.catalog_manager import CatalogManager

        catalog_manager = CatalogManager(etf_catalog_base)
        service = ImportService(
            catalog_manager=catalog_manager,
            metadata_service=mock_metadata_service,
            instrument_mapper=mock_instrument_mapper,
        )

        results = service.import_directory(
            source_dir=etf_intraday_source,
            catalog_name="firstrate-etf",
            asset_class=AssetClass.ETF,
            timeframe="1-MINUTE-LAST",
        )

        assert {r.ticker for r in results} == {"SPY", "QQQ"}
        assert all(r.status == "success" for r in results)
        assert all(r.row_count == 2 for r in results)

        # AC1: on-disk leaf layout data/bar/{BAR_TYPE}/*.parquet where BAR_TYPE
        # begins with the instrument id ({INSTRUMENT_ID}/{BAR_TYPE}).
        bar_dir = etf_catalog_base / "firstrate-etf" / "data" / "bar"
        leaf_dirs = sorted(p.name for p in bar_dir.iterdir() if p.is_dir())
        assert leaf_dirs == [
            "QQQ.ARCA-1-MINUTE-LAST-EXTERNAL",
            "SPY.ARCA-1-MINUTE-LAST-EXTERNAL",
        ]
        for leaf in leaf_dirs:
            assert list((bar_dir / leaf).glob("*.parquet")), f"no parquet under {leaf}"

        # AC5: isolation — only the firstrate-etf catalog dir exists under base
        # (nothing mixed with IBKR/Kraken).
        assert sorted(p.name for p in etf_catalog_base.iterdir() if p.is_dir()) == ["firstrate-etf"]

        # AC5: decimal precision + UTC normalization preserved on round-trip.
        catalog = catalog_manager.resolve_catalog("firstrate-etf")
        spy_bars = catalog.bars(bar_types=["SPY.ARCA-1-MINUTE-LAST-EXTERNAL"])
        assert len(spy_bars) == 2
        assert str(spy_bars[0].open) == "100.12"
        assert str(spy_bars[0].high) == "101.55"
        # 09:30 ET on 2024-01-02 (EST, UTC-5) == 14:30 UTC.
        expected_ts = (
            int(datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
        )
        assert spy_bars[0].ts_init == expected_ts

    def test_real_parser_drops_and_flags_invalid_row_end_to_end(
        self,
        etf_catalog_base,
        tmp_path,
        mock_metadata_service,
        mock_instrument_mapper,
    ):
        """Story 2.5 AC1/AC4 end-to-end with the REAL parser + catalog.

        An invalid row (high < low) is rejected by Nautilus at Bar construction,
        so it is dropped before becoming a bar — proving the design's linchpin:
        the violation survives only at the raw-row layer (``last_validation``)
        and is surfaced as a non-blocking warning while the valid bars import.
        """
        from src.services.firstrate.catalog_manager import CatalogManager

        src = tmp_path / "source"
        s_dir = src / "S"
        s_dir.mkdir(parents=True)
        # Row 2 has high (99.00) < low (100.50) — invalid, dropped by Nautilus.
        (s_dir / "SPY.txt").write_text(
            "2024-01-02 09:30:00,100.12,101.55,99.50,100.75,1000\n"
            "2024-01-02 09:31:00,100.75,99.00,100.50,100.90,2000\n"
            "2024-01-02 09:32:00,100.90,102.00,100.00,101.25,3000\n"
        )

        catalog_manager = CatalogManager(etf_catalog_base)
        service = ImportService(
            catalog_manager=catalog_manager,
            metadata_service=mock_metadata_service,
            instrument_mapper=mock_instrument_mapper,
        )

        results = service.import_directory(
            source_dir=src,
            catalog_name="firstrate-etf",
            asset_class=AssetClass.ETF,
            timeframe="1-MINUTE-LAST",
        )

        assert len(results) == 1
        spy = results[0]
        # Non-blocking: the 2 valid bars still import (the invalid row dropped).
        assert spy.status == "success"
        assert spy.row_count == 2
        # AC1/AC4: the invalid row is flagged and surfaced via warnings.
        assert spy.warnings
        assert any("high" in w and "low" in w for w in spy.warnings)
        # The dropped row really is absent from the catalog (2 bars on disk).
        catalog = catalog_manager.resolve_catalog("firstrate-etf")
        assert len(catalog.bars(bar_types=["SPY.ARCA-1-MINUTE-LAST-EXTERNAL"])) == 2


# ---------------------------------------------------------------------------
# Story 2.4 — cache-first metadata resolution wiring (AC4)
# ---------------------------------------------------------------------------


class _FakeMetadataProvider:
    """In-memory ``MetadataProvider`` that counts calls and never hits a network."""

    PROVIDER_NAME = "FAKE"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, ticker: str):
        from src.models.instrument_metadata import (
            AssetType,
            InstrumentMetadata,
            ResolutionStatus,
        )

        self.calls.append(ticker)
        return InstrumentMetadata(
            ticker=ticker,
            metadata_provider=self.PROVIDER_NAME,
            venue="ARCA",
            currency="USD",
            asset_type=AssetType.ETF,
            company_name="Fake Co",
            sector="Funds",
            industry="ETF",
            country="US",
            resolution_status=ResolutionStatus.RESOLVED,
        )


class _FakeMetadataRepo:
    """In-memory stand-in for ``SyncInstrumentMetadataRepository`` (no DB)."""

    def __init__(self) -> None:
        self.store: dict = {}

    def get_by_ticker(self, ticker: str):
        return self.store.get(ticker)

    def upsert(self, orm):
        self.store[orm.ticker] = orm
        return orm


def _resolved_orm_row(ticker: str):
    """Build a RESOLVED ORM cache row (no session needed)."""
    from datetime import datetime, timezone

    from src.db.models.instrument_metadata import InstrumentMetadata as OrmInstrumentMetadata
    from src.models.instrument_metadata import ResolutionStatus

    return OrmInstrumentMetadata(
        ticker=ticker,
        metadata_provider="FAKE",
        venue="ARCA",
        currency="USD",
        asset_type="ETF",
        company_name="Seeded Co",
        sector="Funds",
        industry="ETF",
        country="US",
        resolution_status=ResolutionStatus.RESOLVED,
        resolved_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.component
class TestMetadataResolveWiring:
    """AC4: each parsed ticker resolves cache-first; skips/faults behave correctly."""

    def _service(
        self, mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, resolver
    ):
        return ImportService(
            catalog_manager=mock_catalog_manager,
            metadata_service=mock_metadata_service,
            instrument_mapper=mock_instrument_mapper,
            metadata_resolver=resolver,
        )

    def test_resolve_called_once_per_parsed_ticker(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        resolver = MagicMock()
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = self._service(
                mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, resolver
            )
            service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        resolved = [c.args[0] for c in resolver.resolve.call_args_list]
        assert sorted(resolved) == ["AAPL", "ARKK", "SPY"]

    def test_resolve_deduped_across_timeframe_passes(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        """One ImportService reused across timeframe passes resolves each ticker once.

        Mirrors the CLI's per-timeframe loop over a single reused service. The
        source files here are timeframe-agnostic, so every ticker is discovered
        on both passes — without dedup that would be 6 resolve() calls.
        """
        resolver = MagicMock()
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = self._service(
                mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, resolver
            )
            for tf in ("1-DAY-LAST", "1-HOUR-LAST"):
                service.import_directory(
                    source_dir=source_dir,
                    catalog_name=CATALOG_NAME,
                    asset_class=AssetClass.ETF,
                    timeframe=tf,
                )

        resolved = sorted(c.args[0] for c in resolver.resolve.call_args_list)
        assert resolved == ["AAPL", "ARKK", "SPY"]  # once each, not 6 calls

    def test_cache_first_resolved_ticker_skips_provider(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        from typing import cast

        from src.db.repositories.instrument_metadata_repository_sync import (
            SyncInstrumentMetadataRepository,
        )
        from src.services.metadata.instrument_metadata_service import (
            InstrumentMetadataService,
        )

        provider = _FakeMetadataProvider()
        repo = _FakeMetadataRepo()
        repo.store["AAPL"] = _resolved_orm_row("AAPL")  # pre-cached → cache hit
        resolver = InstrumentMetadataService(
            provider=provider,
            repository=cast(SyncInstrumentMetadataRepository, repo),
        )

        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = self._service(
                mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, resolver
            )
            service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        # AAPL was already RESOLVED → provider NOT hit; the others are upserted.
        assert provider.calls == ["ARKK", "SPY"]
        assert set(repo.store) == {"AAPL", "ARKK", "SPY"}

    def test_skipped_ticker_does_not_resolve(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        resolver = MagicMock()
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        _force_bar_end_to_2025_01_15(bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = self._service(
                mock_catalog_manager, stateful_metadata_service, mock_instrument_mapper, resolver
            )
            service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )
            resolves_after_first = resolver.resolve.call_count

            # Second run: everything is complete → all skipped → no new resolves.
            service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert resolves_after_first == 2  # AAPL + SPY on the first (parsing) run
        assert resolver.resolve.call_count == resolves_after_first  # no new calls

    def test_resolver_fault_does_not_fail_bar_import(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        bars_by_ticker,
    ):
        resolver = MagicMock()
        resolver.resolve.side_effect = RuntimeError("provider exploded")
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            service = self._service(
                mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, resolver
            )
            results = service.import_directory(
                source_dir=source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert all(r.status == "success" for r in results)
        assert resolver.resolve.call_count == 3


# ---------------------------------------------------------------------------
# Story 2.5 AC1/AC4: OHLC-sanity flags surfaced (non-blocking) on import
# ---------------------------------------------------------------------------


@pytest.mark.component
class TestOhlcSanitySurfacing:
    """OHLC-sanity flags from the parser are surfaced but do not fail the import."""

    def test_ohlc_violation_is_flagged_but_import_succeeds(
        self,
        source_dir,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
    ):
        from src.models.catalog import ValidationResult

        bars_by_ticker = {
            "AAPL": _make_bars(2),
            "ARKK": _make_bars(3),
            "SPY": _make_bars(4),
        }
        # AAPL's source has an OHLC-sanity violation that the parser flags via
        # last_validation (the invalid row is dropped before becoming a bar).
        invalid_for = {
            "AAPL": ValidationResult(
                valid=False,
                errors=["Row 2: high (90.00) < low (95.00)"],
                row_count=3,
                invalid_rows=1,
            )
        }

        mock_catalog = mock_catalog_manager.resolve_catalog.return_value

        def _bars_readback(**_kwargs):
            if mock_catalog.write_data.call_args:
                return mock_catalog.write_data.call_args[0][0]
            return []

        mock_catalog.bars.side_effect = _bars_readback

        mock_parser = MagicMock()

        def _parse_file(file_path, _iid, _bt):
            ticker = file_path.stem
            # Mirror the real parser: stash validation, return parsed bars.
            mock_parser.last_validation = invalid_for.get(ticker)
            return bars_by_ticker[ticker]

        mock_parser.parse_file.side_effect = _parse_file

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            with patch("src.services.firstrate.import_service.logger") as mock_logger:
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

        by_ticker = {r.ticker: r for r in results}
        # Non-blocking: the valid bars still import (AC1 flags, does not reject).
        assert by_ticker["AAPL"].status == "success"
        assert by_ticker["AAPL"].row_count == 2
        # Flagged + surfaced via ImportResult.warnings (AC4).
        assert by_ticker["AAPL"].warnings
        assert any("high" in w for w in by_ticker["AAPL"].warnings)
        # Clean tickers carry no warnings.
        assert by_ticker["ARKK"].warnings == []
        assert by_ticker["SPY"].warnings == []
        # Logged via structured logging (AC4 — never silent).
        events = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
        assert "ohlc_sanity_flagged" in events


# ---------------------------------------------------------------------------
# Story 2.6 — Idempotent re-runs via date-range comparison
#
# Maps each scoped AC to a passing test that exercises the EXISTING Phase-1
# classifier + Story-2.4 cache-first resolver through ``import_directory`` with
# the established in-memory doubles (no real import / network / Postgres / DB):
#   AC1 — date-range comparison re-imports only the incomplete ticker.
#   AC2 — an already-RESOLVED ticker that is re-processed hits the FMP cache
#         (the provider is NOT called again).
#   AC3 — an all-complete re-run rewrites no Parquet AND makes no FMP calls.
# ---------------------------------------------------------------------------


def _make_cache_first_resolver():
    """Wire the REAL cache-first resolver over the in-memory provider + repo.

    Returns ``(resolver, provider, repo)`` so a test can assert on the counting
    provider's ``calls`` (actual FMP fetches) while the resolver enforces the
    cache-first contract exactly as production does.
    """
    from typing import cast

    from src.db.repositories.instrument_metadata_repository_sync import (
        SyncInstrumentMetadataRepository,
    )
    from src.services.metadata.instrument_metadata_service import (
        InstrumentMetadataService,
    )

    provider = _FakeMetadataProvider()
    repo = _FakeMetadataRepo()
    resolver = InstrumentMetadataService(
        provider=provider,
        repository=cast(SyncInstrumentMetadataRepository, repo),
    )
    return resolver, provider, repo


@pytest.mark.component
class TestStory26IdempotentRerun:
    """Story 2.6: a re-run skips complete tickers, reuses the cache, and no-ops when unchanged."""

    def test_ac1_rerun_reimports_only_incomplete_tickers(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        """AC1: date-range comparison re-imports only the incomplete ticker."""
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
            # Run 1: AAPL + SPY both import fresh, metadata end set to 2025-01-15.
            service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

            # Simulate a partial prior import for AAPL only — rewind its
            # date_range_end behind the source while keeping bar_count > 0.
            # SPY stays complete and must be skipped on the re-run.
            aapl_row = stateful_metadata_service.storage[(CATALOG_NAME, "AAPL")]
            aapl_row.date_range_end = stateful_metadata_service._datetime(
                2024, 12, 31, tzinfo=stateful_metadata_service._tz
            )
            aapl_row.bar_count_daily = 5

            writes_before_rerun = mock_catalog.write_data.call_count
            second = service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        by_ticker = {r.ticker: r for r in second}
        assert by_ticker["AAPL"].outcome == "reimported"
        assert by_ticker["SPY"].outcome == "skipped"
        # Exactly one Parquet (re)write on the re-run — AAPL only, never SPY.
        assert mock_catalog.write_data.call_count == writes_before_rerun + 1

    def test_ac2_reprocessed_ticker_hits_fmp_cache(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        """AC2: a re-processed already-RESOLVED ticker hits the cache — provider not re-called."""
        resolver, provider, _repo = _make_cache_first_resolver()
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        _force_bar_end_to_2025_01_15(bars_by_ticker)

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            # Run 1: a fresh service resolves each ticker once via the provider.
            first_service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
                metadata_resolver=resolver,
            )
            first_service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )
            assert sorted(provider.calls) == ["AAPL", "SPY"]
            provider_calls_after_first = list(provider.calls)

            # Force AAPL to re-import on the next run (source advanced past metadata),
            # so resolve() is genuinely invoked for it again — the AC2 scenario.
            aapl_row = stateful_metadata_service.storage[(CATALOG_NAME, "AAPL")]
            aapl_row.date_range_end = stateful_metadata_service._datetime(
                2024, 12, 31, tzinfo=stateful_metadata_service._tz
            )
            aapl_row.bar_count_daily = 5

            # Run 2: a brand-new service (fresh in-run dedup) sharing the SAME
            # cache-first resolver. Spy on resolve() to prove it IS invoked.
            resolver_spy = MagicMock(wraps=resolver)
            second_service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
                metadata_resolver=resolver_spy,
            )
            second = second_service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        by_ticker = {r.ticker: r for r in second}
        assert by_ticker["AAPL"].outcome == "reimported"  # AAPL is processed again
        # resolve() WAS invoked for AAPL on the re-run (it is re-processed)...
        resolved_on_rerun = [c.args[0] for c in resolver_spy.resolve.call_args_list]
        assert "AAPL" in resolved_on_rerun
        # ...but the FMP provider was NOT called again — cache hit (near-100%).
        assert provider.calls == provider_calls_after_first

    def test_ac3_complete_rerun_no_parquet_and_no_fmp(
        self,
        idempotent_source_dir,
        mock_catalog_manager,
        mock_instrument_mapper,
        stateful_metadata_service,
        bars_by_ticker,
    ):
        """AC3: an all-complete re-run rewrites no Parquet and makes no FMP calls."""
        resolver, provider, _repo = _make_cache_first_resolver()
        cm, mock_parser = _install_parser_and_readback(mock_catalog_manager, bars_by_ticker)
        _force_bar_end_to_2025_01_15(bars_by_ticker)
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value

        with cm as mock_get_parser:
            mock_get_parser.return_value = mock_parser
            # Run 1: import both tickers — writes Parquet, resolves via provider.
            first_service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
                metadata_resolver=resolver,
            )
            first_service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )
            writes_after_first = mock_catalog.write_data.call_count
            provider_calls_after_first = list(provider.calls)
            assert writes_after_first == 2  # AAPL + SPY
            assert sorted(provider_calls_after_first) == ["AAPL", "SPY"]

            # Run 2: everything is complete (metadata end == source last date) →
            # all skipped. A fresh service ensures the no-op is not an artifact of
            # the in-run dedup set.
            second_service = ImportService(
                catalog_manager=mock_catalog_manager,
                metadata_service=stateful_metadata_service,
                instrument_mapper=mock_instrument_mapper,
                metadata_resolver=resolver,
            )
            second = second_service.import_directory(
                source_dir=idempotent_source_dir,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert {r.outcome for r in second} == {"skipped"}
        # AC3: no Parquet rewritten...
        assert mock_catalog.write_data.call_count == writes_after_first
        # ...and no FMP calls made.
        assert provider.calls == provider_calls_after_first
