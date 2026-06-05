"""Unit tests for ImportService — per-ticker import orchestration."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass, ImportResult
from src.services.firstrate.import_service import (
    ImportService,
    _bar_count_field_for_timeframe,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CATALOG_NAME = "firstrate-etf"


@pytest.fixture()
def mock_catalog_manager():
    """Return a mock CatalogManager."""
    return MagicMock()


def _fresh_instrument_row() -> MagicMock:
    """Return a mock CatalogInstrument in a fresh/new state.

    Story 1-7's classifier reads ``date_range_end`` and the
    ``bar_count_<tf>`` fields with real comparison operators, so tests
    that want the "new" branch must supply concrete values (not bare
    ``MagicMock`` auto-attrs that raise ``TypeError`` on ``<=``).
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
    """Return a mock MetadataService.

    Defaults ``get_instrument_sync`` to a fresh/new instrument row so
    the story 1-7 classifier routes tickers through the full import
    path (outcome ``"new"``). Tests that want the skipped/reimported
    branches override this on a case-by-case basis.
    """
    service = MagicMock()
    service.get_instrument_sync.return_value = _fresh_instrument_row()
    return service


@pytest.fixture()
def mock_instrument_mapper():
    """Return a mock InstrumentMapper."""
    mapper = MagicMock()
    mapper.is_loaded.return_value = True
    return mapper


@pytest.fixture()
def service(mock_catalog_manager, mock_metadata_service, mock_instrument_mapper):
    """Return an ImportService with mocked dependencies."""
    return ImportService(
        catalog_manager=mock_catalog_manager,
        metadata_service=mock_metadata_service,
        instrument_mapper=mock_instrument_mapper,
    )


# ---------------------------------------------------------------------------
# Task 1: Constructor and method signature
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportServiceInit:
    """Test ImportService constructor accepts dependencies."""

    def test_accepts_all_dependencies(
        self,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
    ):
        svc = ImportService(
            catalog_manager=mock_catalog_manager,
            metadata_service=mock_metadata_service,
            instrument_mapper=mock_instrument_mapper,
        )
        assert svc._catalog_manager is mock_catalog_manager
        assert svc._metadata_service is mock_metadata_service
        assert svc._instrument_mapper is mock_instrument_mapper

    def test_import_directory_returns_list_of_import_results(self, service, tmp_path):
        """import_directory returns list[ImportResult]."""
        results = service.import_directory(
            source_dir=tmp_path,
            catalog_name=CATALOG_NAME,
            asset_class=AssetClass.ETF,
        )
        assert isinstance(results, list)

    def test_raises_on_missing_source_dir(self, service, tmp_path):
        """import_directory raises FileNotFoundError for nonexistent dir."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError, match="does not exist"):
            service.import_directory(
                source_dir=missing,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

    def test_raises_when_mapper_not_loaded(
        self, mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, tmp_path
    ):
        """import_directory raises ValueError if instrument mapper not loaded."""
        mock_instrument_mapper.is_loaded.return_value = False
        svc = ImportService(
            catalog_manager=mock_catalog_manager,
            metadata_service=mock_metadata_service,
            instrument_mapper=mock_instrument_mapper,
        )
        with pytest.raises(ValueError, match="not loaded"):
            svc.import_directory(
                source_dir=tmp_path,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )


# ---------------------------------------------------------------------------
# Task 2: Directory traversal and ticker discovery
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_dir_with_tickers(tmp_path):
    """Create a directory structure with alphabetical subdirs and .txt files."""
    a_dir = tmp_path / "A"
    a_dir.mkdir()
    (a_dir / "AAPL.txt").write_text("data")
    (a_dir / "ARKK.txt").write_text("data")

    s_dir = tmp_path / "S"
    s_dir.mkdir()
    (s_dir / "SPY.txt").write_text("data")

    return tmp_path


@pytest.mark.unit
class TestDiscoverTickers:
    """Test _discover_tickers directory traversal."""

    def test_discovers_tickers_from_alpha_subdirs(self, service, source_dir_with_tickers):
        result = service._discover_tickers(source_dir_with_tickers)
        tickers = [t for t, _ in result]
        assert tickers == ["AAPL", "ARKK", "SPY"]

    def test_returns_correct_file_paths(self, service, source_dir_with_tickers):
        result = service._discover_tickers(source_dir_with_tickers)
        paths = [p for _, p in result]
        assert all(p.suffix == ".txt" for p in paths)
        assert paths[0].name == "AAPL.txt"
        assert paths[2].name == "SPY.txt"

    def test_empty_directory(self, service, tmp_path):
        result = service._discover_tickers(tmp_path)
        assert result == []

    def test_no_txt_files_in_subdirs(self, service, tmp_path):
        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "readme.md").write_text("not data")
        result = service._discover_tickers(tmp_path)
        assert result == []

    def test_skips_files_in_root(self, service, tmp_path):
        """Files directly in source_dir (not in subdirs) are skipped."""
        (tmp_path / "loose.txt").write_text("data")
        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "AAPL.txt").write_text("data")
        result = service._discover_tickers(tmp_path)
        assert len(result) == 1
        assert result[0][0] == "AAPL"

    def test_extracts_ticker_from_firstrate_filename_pattern(self, service, tmp_path):
        """FirstRate filenames ({TICKER}_full_{tf}_adjsplitdiv.txt) yield just {TICKER}."""
        stock_a = tmp_path / "stock_A_full_1day_adjsplitdiv_abc123"
        stock_a.mkdir()
        (stock_a / "AAPL_full_1day_adjsplitdiv.txt").write_text("data")
        (stock_a / "A_full_1day_adjsplitdiv.txt").write_text("data")

        result = service._discover_tickers(tmp_path)
        tickers = [t for t, _ in result]
        assert "AAPL" in tickers
        assert "A" in tickers
        # Must NOT contain the full stem
        assert "AAPL_full_1day_adjsplitdiv" not in tickers

    def test_firstrate_multiple_timeframes_in_stem(self, service, tmp_path):
        """Different timeframe suffixes all resolve to the same ticker."""
        hourly = tmp_path / "stock_S_full_1hour_adjsplitdiv_xyz789"
        hourly.mkdir()
        (hourly / "SPY_full_1hour_adjsplitdiv.txt").write_text("data")

        minute = tmp_path / "stock_S_full_1min_adjsplitdiv_qrs456"
        minute.mkdir()
        (minute / "SPY_full_1min_adjsplitdiv.txt").write_text("data")

        result = service._discover_tickers(tmp_path)
        tickers = [t for t, _ in result]
        assert tickers == ["SPY", "SPY"]

    def test_non_firstrate_filename_falls_back_to_stem(self, service, tmp_path):
        """Filenames without '_full_' marker keep using file.stem."""
        subdir = tmp_path / "A"
        subdir.mkdir()
        (subdir / "AAPL.txt").write_text("data")
        result = service._discover_tickers(tmp_path)
        assert result[0][0] == "AAPL"


# ---------------------------------------------------------------------------
# Task 3: Per-ticker import orchestration
# ---------------------------------------------------------------------------


def _make_mock_bar(ts_init: int = 1_000_000_000_000_000_000) -> MagicMock:
    """Create a mock Bar with ts_init."""
    bar = MagicMock()
    bar.ts_init = ts_init
    bar.bar_type = MagicMock()
    return bar


@pytest.fixture()
def mock_bars():
    """Return a list of 3 mock bars with ascending timestamps."""
    return [
        _make_mock_bar(1_577_836_800_000_000_000),  # 2020-01-01
        _make_mock_bar(1_577_923_200_000_000_000),  # 2020-01-02
        _make_mock_bar(1_578_009_600_000_000_000),  # 2020-01-03
    ]


@pytest.fixture()
def configured_service(
    mock_catalog_manager, mock_metadata_service, mock_instrument_mapper, mock_bars
):
    """ImportService with dependencies configured for a successful import."""
    instrument_id = InstrumentId.from_str("SPY.ARCA")
    mock_instrument_mapper.resolve_instrument_id.return_value = instrument_id

    mock_catalog = MagicMock()
    mock_catalog_manager.resolve_catalog.return_value = mock_catalog
    # read-back returns same bars (verification passes)
    mock_catalog.bars.return_value = mock_bars

    # Fresh instrument row → classifier picks "new", full import path runs.
    mock_metadata_service.get_instrument_sync.return_value = _fresh_instrument_row()

    return ImportService(
        catalog_manager=mock_catalog_manager,
        metadata_service=mock_metadata_service,
        instrument_mapper=mock_instrument_mapper,
    )


@pytest.mark.unit
class TestImportTicker:
    """Test _import_ticker per-ticker orchestration."""

    def test_successful_import_returns_success_result(
        self, configured_service, mock_bars, tmp_path
    ):
        csv_file = tmp_path / "SPY.txt"
        csv_file.write_text("data")

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert isinstance(result, ImportResult)
        assert result.status == "success"
        assert result.ticker == "SPY"
        assert result.row_count == 3
        assert result.duration >= 0

    def test_import_calls_write_data(
        self, configured_service, mock_catalog_manager, mock_bars, tmp_path
    ):
        csv_file = tmp_path / "SPY.txt"
        csv_file.write_text("data")

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            configured_service._import_ticker(
                ticker="SPY",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        mock_catalog = mock_catalog_manager.resolve_catalog.return_value
        mock_catalog.write_data.assert_called_once_with(mock_bars)

    def test_import_deletes_existing_partition_before_write(
        self, configured_service, mock_catalog_manager, mock_bars, tmp_path
    ):
        """Re-import must clear the bar_type partition before writing.

        Code review finding #1: Nautilus ``write_data`` appends a new parquet
        part. Without a delete first, a re-import (or orphan-heal) duplicates
        every bar on disk and corrupts downstream PnL/drawdown. The delete must
        target the full bar_type identifier and happen *before* the write.
        """
        from nautilus_trader.model.data import Bar

        csv_file = tmp_path / "SPY.txt"
        csv_file.write_text("data")

        mock_catalog = mock_catalog_manager.resolve_catalog.return_value
        call_order: list[str] = []
        mock_catalog.delete_data_range.side_effect = lambda *a, **k: call_order.append("delete")
        mock_catalog.write_data.side_effect = lambda *a, **k: call_order.append("write")

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            configured_service._import_ticker(
                ticker="SPY",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert call_order == ["delete", "write"]
        delete_kwargs = mock_catalog.delete_data_range.call_args.kwargs
        assert delete_kwargs["data_cls"] is Bar
        assert delete_kwargs["identifier"] == "SPY.ARCA-1-DAY-LAST-EXTERNAL"

    def test_import_tracks_duration(self, configured_service, mock_bars, tmp_path):
        csv_file = tmp_path / "SPY.txt"
        csv_file.write_text("data")

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert result.duration >= 0


# ---------------------------------------------------------------------------
# Task 4: Row count verification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyRowCount:
    """Test _verify_row_count comparison."""

    def test_matching_counts_returns_true(self, service):
        bars_a = [_make_mock_bar() for _ in range(5)]
        bars_b = [_make_mock_bar() for _ in range(5)]
        assert service._verify_row_count(bars_a, bars_b) is True

    def test_mismatched_counts_returns_false(self, service):
        bars_a = [_make_mock_bar() for _ in range(5)]
        bars_b = [_make_mock_bar() for _ in range(3)]
        assert service._verify_row_count(bars_a, bars_b) is False

    def test_empty_lists_match(self, service):
        assert service._verify_row_count([], []) is True


# ---------------------------------------------------------------------------
# Task 5: Sample point validation
# ---------------------------------------------------------------------------


def _make_bar_with_ohlcv(
    ts_init: int = 1_000_000_000_000_000_000,
    open_price: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
) -> MagicMock:
    """Create a mock bar with specific OHLCV values."""
    bar = MagicMock()
    bar.ts_init = ts_init
    bar.open = open_price
    bar.high = high
    bar.low = low
    bar.close = close
    bar.volume = volume
    return bar


@pytest.mark.unit
class TestVerifySamplePoints:
    """Test _verify_sample_points first/last N bar comparison."""

    def test_identical_bars_pass(self, service):
        bars = [_make_bar_with_ohlcv(ts_init=i * 10**18) for i in range(5)]
        assert service._verify_sample_points(bars, bars) is True

    def test_mismatched_head_fails(self, service):
        source = [_make_bar_with_ohlcv(ts_init=i * 10**18) for i in range(5)]
        catalog = [_make_bar_with_ohlcv(ts_init=i * 10**18) for i in range(5)]
        catalog[0].ts_init = 999  # mismatch first bar
        assert service._verify_sample_points(source, catalog) is False

    def test_mismatched_tail_fails(self, service):
        source = [_make_bar_with_ohlcv(ts_init=i * 10**18) for i in range(15)]
        catalog = [_make_bar_with_ohlcv(ts_init=i * 10**18) for i in range(15)]
        catalog[-1].close = 999.99  # mismatch last bar
        assert service._verify_sample_points(source, catalog) is False

    def test_small_list_uses_actual_size(self, service):
        """When fewer than sample_size bars, compare all."""
        bars = [_make_bar_with_ohlcv(ts_init=i * 10**18) for i in range(3)]
        assert service._verify_sample_points(bars, bars, sample_size=10) is True

    def test_precision_preserved(self, service):
        """Exact decimal values must match — no float drift."""
        bar_a = _make_bar_with_ohlcv(open_price=123.456789)
        bar_b = _make_bar_with_ohlcv(open_price=123.456789)
        assert service._verify_sample_points([bar_a], [bar_b]) is True

        bar_c = _make_bar_with_ohlcv(open_price=123.456789)
        bar_d = _make_bar_with_ohlcv(open_price=123.456788)
        assert service._verify_sample_points([bar_c], [bar_d]) is False


# ---------------------------------------------------------------------------
# Task 6: Metadata upsert after verification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpsertMetadata:
    """Test _upsert_metadata updates correct fields."""

    def test_updates_date_range_and_bar_count(
        self, configured_service, mock_metadata_service, mock_bars
    ):
        existing = MagicMock()
        existing.bar_count_daily = 0
        mock_metadata_service.get_instrument_sync.return_value = existing

        result = configured_service._upsert_metadata(
            ticker="SPY",
            catalog_name=CATALOG_NAME,
            bars=mock_bars,
            timeframe="1-DAY-LAST",
        )

        assert result is True
        assert existing.date_range_start is not None
        assert existing.date_range_end is not None
        assert existing.bar_count_daily == 3
        mock_metadata_service.upsert_instrument_sync.assert_called_once_with(existing)

    def test_hourly_timeframe_sets_bar_count_hourly(
        self, configured_service, mock_metadata_service, mock_bars
    ):
        existing = MagicMock()
        existing.bar_count_hourly = 0
        mock_metadata_service.get_instrument_sync.return_value = existing

        result = configured_service._upsert_metadata(
            ticker="SPY",
            catalog_name=CATALOG_NAME,
            bars=mock_bars,
            timeframe="1-HOUR-LAST",
        )

        assert result is True
        assert existing.bar_count_hourly == 3

    def test_5min_timeframe_sets_bar_count_5min(
        self, configured_service, mock_metadata_service, mock_bars
    ):
        existing = MagicMock()
        existing.bar_count_5min = 0
        mock_metadata_service.get_instrument_sync.return_value = existing

        result = configured_service._upsert_metadata(
            ticker="SPY",
            catalog_name=CATALOG_NAME,
            bars=mock_bars,
            timeframe="5-MINUTE-LAST",
        )

        assert result is True
        assert existing.bar_count_5min == 3

    def test_returns_false_when_no_existing_instrument(
        self, configured_service, mock_metadata_service, mock_bars
    ):
        mock_metadata_service.get_instrument_sync.return_value = None

        result = configured_service._upsert_metadata(
            ticker="MISSING",
            catalog_name=CATALOG_NAME,
            bars=mock_bars,
        )

        assert result is False
        mock_metadata_service.upsert_instrument_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Task 7: Error isolation and continuation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorIsolation:
    """Test error handling in _import_ticker."""

    def test_parse_failure_returns_failed_result(self, configured_service, tmp_path):
        csv_file = tmp_path / "BAD.txt"
        csv_file.write_text("data")

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.side_effect = ValueError("Bad CSV format")
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="BAD",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert result.status == "failed"
        assert "Bad CSV format" in result.error
        assert result.duration >= 0

    def test_write_failure_returns_failed_result(
        self, configured_service, mock_catalog_manager, mock_bars, tmp_path
    ):
        csv_file = tmp_path / "SPY.txt"
        csv_file.write_text("data")
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value
        mock_catalog.write_data.side_effect = OSError("Disk full")

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert result.status == "failed"
        assert "Disk full" in result.error

    def test_instrument_mapping_failure_returns_failed(
        self,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        tmp_path,
    ):
        from src.db.exceptions import InstrumentMappingError

        mock_instrument_mapper.resolve_instrument_id.side_effect = InstrumentMappingError(
            "Not found"
        )
        svc = ImportService(
            catalog_manager=mock_catalog_manager,
            metadata_service=mock_metadata_service,
            instrument_mapper=mock_instrument_mapper,
        )
        csv_file = tmp_path / "UNKNOWN.txt"
        csv_file.write_text("data")

        result = svc._import_ticker(
            ticker="UNKNOWN",
            file_path=csv_file,
            catalog_name=CATALOG_NAME,
            asset_class=AssetClass.ETF,
        )

        assert result.status == "failed"
        assert "Not found" in result.error

    def test_verification_failure_skips_metadata_upsert(
        self,
        configured_service,
        mock_catalog_manager,
        mock_metadata_service,
        mock_bars,
        tmp_path,
    ):
        csv_file = tmp_path / "SPY.txt"
        csv_file.write_text("data")
        # Return mismatched count to trigger verification failure
        mock_catalog = mock_catalog_manager.resolve_catalog.return_value
        mock_catalog.bars.return_value = mock_bars[:1]  # fewer bars

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv_file,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
            )

        assert result.status == "failed"
        assert "Row count mismatch" in result.error
        # Metadata gatekeeper: no upsert on failure
        mock_metadata_service.upsert_instrument_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Story 1-7 — Idempotent classifier
# ---------------------------------------------------------------------------


def _metadata_with(
    date_range_end: datetime | None,
    bar_count_daily: int = 0,
    bar_count_hourly: int = 0,
    bar_count_minute: int = 0,
    bar_count_5min: int = 0,
) -> MagicMock:
    """Build a mock CatalogInstrument with explicit classifier-relevant fields."""
    instrument = MagicMock()
    instrument.date_range_end = date_range_end
    instrument.bar_count_daily = bar_count_daily
    instrument.bar_count_hourly = bar_count_hourly
    instrument.bar_count_minute = bar_count_minute
    instrument.bar_count_5min = bar_count_5min
    return instrument


def _write_daily_csv(path, last_day: str) -> None:
    """Write a single-row daily FirstRate CSV ending on ``last_day``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"2000-01-01,100,101,99,100,1000\n{last_day},101,102,100,101,1100\n",
        encoding="utf-8",
    )


@pytest.mark.unit
class TestBarCountFieldForTimeframe:
    """Sanity-check the module-level helper used by the classifier."""

    @pytest.mark.parametrize(
        ("timeframe", "field"),
        [
            ("1-DAY-LAST", "bar_count_daily"),
            ("1-HOUR-LAST", "bar_count_hourly"),
            ("1-MINUTE-LAST", "bar_count_minute"),
            ("5-MINUTE-LAST", "bar_count_5min"),
            # Unknown aggregations degrade to daily to match _upsert_metadata.
            ("1-SECOND-LAST", "bar_count_daily"),
            ("noop", "bar_count_daily"),
        ],
    )
    def test_maps_timeframe_to_bar_count_field(self, timeframe, field):
        assert _bar_count_field_for_timeframe(timeframe) == field


@pytest.mark.unit
class TestClassifyTicker:
    """Per-edge-case coverage for ``ImportService._classify_ticker``."""

    def test_complete_ticker_is_skipped(self, service, mock_metadata_service, tmp_path):
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-15")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=42,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-DAY-LAST",
        )

        assert decision == "skipped"

    def test_source_ahead_of_metadata_is_reimported(self, service, mock_metadata_service, tmp_path):
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-20")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=42,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-DAY-LAST",
        )

        assert decision == "reimported"

    def test_source_behind_metadata_is_reimported_with_warning(
        self, service, mock_metadata_service, tmp_path
    ):
        """Stale/regressed source must never be silently skipped."""
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-10")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=42,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-DAY-LAST",
        )

        assert decision == "reimported"

    def test_orphan_metadata_row_is_new(self, service, mock_metadata_service, tmp_path):
        """date_range_end IS NULL AND bar_count == 0 -> orphan, classify new."""
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-15")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=None,
            bar_count_daily=0,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-DAY-LAST",
        )

        assert decision == "new"

    def test_other_timeframe_imported_this_one_new(self, service, mock_metadata_service, tmp_path):
        """bar_count for *this* timeframe is 0 even though another tf has rows."""
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-15")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=999,  # daily already done
            bar_count_hourly=0,  # but hourly never ran
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-HOUR-LAST",
        )

        assert decision == "new"

    def test_empty_source_returns_new_not_skipped(self, service, mock_metadata_service, tmp_path):
        """Empty/malformed source must fall through to the real import path."""
        csv = tmp_path / "SPY.txt"
        csv.write_text("", encoding="utf-8")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=42,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-DAY-LAST",
        )

        assert decision == "new"

    def test_missing_metadata_row_is_new(self, service, mock_metadata_service, tmp_path):
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-15")
        mock_metadata_service.get_instrument_sync.return_value = None

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-DAY-LAST",
        )

        assert decision == "new"

    def test_intraday_same_day_new_bars_is_reimported(
        self, service, mock_metadata_service, tmp_path
    ):
        """Hourly re-run of a morning import must pick up afternoon bars.

        Code review finding: day-granularity comparison silently skipped
        intraday re-runs on the same UTC day. For HOUR/MINUTE aggregations
        the classifier must compare at full datetime precision.
        """
        csv = tmp_path / "SPY.txt"
        csv.write_text(
            "2025-01-15 09:00:00,100,101,99,100,1000\n2025-01-15 15:00:00,101,102,100,101,1100\n",
            encoding="utf-8",
        )
        # Metadata reflects the morning import ending at 09:00 UTC.
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
            bar_count_hourly=1,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-HOUR-LAST",
        )

        assert decision == "reimported"

    def test_intraday_exact_datetime_match_is_skipped(
        self, service, mock_metadata_service, tmp_path
    ):
        """Intraday re-run where source and metadata share the same last bar.

        FirstRate timestamps are ET; metadata is stored in UTC. The CSV
        row ``2025-01-15 15:00:00`` is 15:00 EST = 20:00 UTC, so the
        metadata's ``date_range_end`` must be in UTC at the matching
        time for the classifier to skip the reimport.
        """
        csv = tmp_path / "SPY.txt"
        csv.write_text(
            "2025-01-15 09:00:00,100,101,99,100,1000\n2025-01-15 15:00:00,101,102,100,101,1100\n",
            encoding="utf-8",
        )
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            # 2025-01-15 15:00 EST = 20:00 UTC
            date_range_end=datetime(2025, 1, 15, 20, 0, 0, tzinfo=timezone.utc),
            bar_count_hourly=2,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-HOUR-LAST",
        )

        assert decision == "skipped"

    def test_intraday_source_behind_metadata_is_reimported(
        self, service, mock_metadata_service, tmp_path
    ):
        """Minute-timeframe source with an earlier last bar still warns + reimports."""
        csv = tmp_path / "SPY.txt"
        csv.write_text(
            "2025-01-15 09:30:00,100,101,99,100,1000\n",
            encoding="utf-8",
        )
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, 15, 45, 0, tzinfo=timezone.utc),
            bar_count_minute=100,
        )

        decision = service._classify_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            timeframe="1-MINUTE-LAST",
        )

        assert decision == "reimported"


@pytest.mark.unit
class TestImportTickerOutcomes:
    """End-to-end wiring from classifier → ``_import_ticker`` outcome."""

    def test_skipped_short_circuits_full_import_path(
        self,
        configured_service,
        mock_catalog_manager,
        mock_metadata_service,
        mock_instrument_mapper,
        tmp_path,
    ):
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-01-15")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=42,
        )

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )
            # Parser never fetched — classifier short-circuited.
            mock_get_parser.assert_not_called()

        assert result.status == "skipped"
        assert result.outcome == "skipped"
        assert result.row_count == 0
        assert result.error is None
        # Downstream services untouched.
        mock_catalog_manager.resolve_catalog.assert_not_called()
        mock_instrument_mapper.resolve_instrument_id.assert_not_called()
        mock_metadata_service.upsert_instrument_sync.assert_not_called()

    def test_reimported_tags_outcome_and_runs_full_path(
        self,
        configured_service,
        mock_catalog_manager,
        mock_metadata_service,
        mock_bars,
        tmp_path,
    ):
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-02-01")
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
            bar_count_daily=42,
        )

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert result.status == "success"
        assert result.outcome == "reimported"
        assert result.row_count == 3
        mock_catalog_manager.resolve_catalog.return_value.write_data.assert_called_once_with(
            mock_bars
        )

    def test_new_tags_outcome_new(
        self, configured_service, mock_metadata_service, mock_bars, tmp_path
    ):
        csv = tmp_path / "SPY.txt"
        _write_daily_csv(csv, "2025-02-01")
        # Default fixture already points at a fresh instrument row, but make
        # it explicit here for readability.
        mock_metadata_service.get_instrument_sync.return_value = _metadata_with(
            date_range_end=None,
            bar_count_daily=0,
        )

        with patch("src.services.firstrate.import_service.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            result = configured_service._import_ticker(
                ticker="SPY",
                file_path=csv,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert result.status == "success"
        assert result.outcome == "new"
        assert result.row_count == 3

    def test_classifier_exception_becomes_failed_result(
        self, configured_service, mock_metadata_service, tmp_path
    ):
        csv = tmp_path / "SPY.txt"
        csv.write_text("noop", encoding="utf-8")
        mock_metadata_service.get_instrument_sync.side_effect = RuntimeError("db down")

        result = configured_service._import_ticker(
            ticker="SPY",
            file_path=csv,
            catalog_name=CATALOG_NAME,
            asset_class=AssetClass.ETF,
            timeframe="1-DAY-LAST",
        )

        assert result.status == "failed"
        assert result.outcome is None
        assert "db down" in (result.error or "")


@pytest.mark.unit
class TestImportDirectoryCompleteLogLine:
    """Story 1-7: import_directory_complete must count skip/reimport/new."""

    def test_log_line_includes_outcome_buckets(
        self, configured_service, mock_metadata_service, mock_bars, tmp_path
    ):
        # Build a tree with two tickers — SPY will be skipped, AAPL will be new.
        spy_dir = tmp_path / "S"
        spy_dir.mkdir()
        _write_daily_csv(spy_dir / "SPY.txt", "2025-01-15")
        aapl_dir = tmp_path / "A"
        aapl_dir.mkdir()
        _write_daily_csv(aapl_dir / "AAPL.txt", "2025-02-01")

        def _fake_lookup(_catalog: str, ticker: str):
            if ticker == "SPY":
                return _metadata_with(
                    date_range_end=datetime(2025, 1, 15, tzinfo=timezone.utc),
                    bar_count_daily=42,
                )
            return _metadata_with(date_range_end=None, bar_count_daily=0)

        mock_metadata_service.get_instrument_sync.side_effect = _fake_lookup

        with (
            patch("src.services.firstrate.import_service.get_parser") as mock_get_parser,
            patch("src.services.firstrate.import_service.logger") as mock_logger,
        ):
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = mock_bars
            mock_get_parser.return_value = mock_parser

            results = configured_service.import_directory(
                source_dir=tmp_path,
                catalog_name=CATALOG_NAME,
                asset_class=AssetClass.ETF,
                timeframe="1-DAY-LAST",
            )

        assert len(results) == 2
        outcomes = sorted(r.outcome or "" for r in results)
        assert outcomes == ["new", "skipped"]

        complete_calls = [
            call.kwargs
            for call in mock_logger.info.call_args_list
            if call.args and call.args[0] == "import_directory_complete"
        ]
        assert len(complete_calls) == 1
        payload = complete_calls[0]
        assert payload["total"] == 2
        assert payload["skipped"] == 1
        assert payload["new"] == 1
        assert payload["reimported"] == 0
        assert payload["failed"] == 0
