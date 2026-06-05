"""Unit tests for Story 4-1 supplementary-data wiring in the import CLI."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.import_data import (
    _discover_supplementary_tickers,
    _find_supplementary_dirs,
)
from src.cli.commands.import_reporting import _print_supplementary_summary
from src.models.catalog import ImportResult
from src.services.firstrate.supplementary_loader import SupplementaryLoadResult


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _find_supplementary_dirs — auto-discovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindSupplementaryDirs:
    def test_found_inside_source_path(self, tmp_path):
        """stock_dividends/stock_splits directly inside source_path are found."""
        (tmp_path / "stock_dividends").mkdir()
        (tmp_path / "stock_splits").mkdir()

        div, split = _find_supplementary_dirs(tmp_path)

        assert div == tmp_path / "stock_dividends"
        assert split == tmp_path / "stock_splits"

    def test_found_as_sibling_via_parent(self, tmp_path):
        """Sibling layout (dirs one level up from source) is discovered."""
        (tmp_path / "stock_dividends").mkdir()
        (tmp_path / "stock_splits").mkdir()
        source = tmp_path / "1day"
        source.mkdir()

        div, split = _find_supplementary_dirs(source)

        assert div == tmp_path / "stock_dividends"
        assert split == tmp_path / "stock_splits"

    def test_etf_variant_found(self, tmp_path):
        """etf_dividends/etf_splits are discovered too."""
        (tmp_path / "etf_dividends").mkdir()
        (tmp_path / "etf_splits").mkdir()

        div, split = _find_supplementary_dirs(tmp_path)

        assert div == tmp_path / "etf_dividends"
        assert split == tmp_path / "etf_splits"

    def test_none_when_absent(self, tmp_path):
        """No supplementary dirs anywhere → (None, None) (AC-6)."""
        source = tmp_path / "data"
        source.mkdir()

        assert _find_supplementary_dirs(source) == (None, None)


# ---------------------------------------------------------------------------
# _discover_supplementary_tickers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverSupplementaryTickers:
    def test_union_of_both_dirs(self, tmp_path):
        """Tickers come from the union of dividend and split files."""
        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()
        (div_dir / "AAPL_divs.txt").write_text("")
        (div_dir / "MSFT_divs.txt").write_text("")
        (split_dir / "AAPL.txt").write_text("")
        (split_dir / "TSLA.txt").write_text("")

        result = _discover_supplementary_tickers(div_dir, split_dir)

        assert result == ["AAPL", "MSFT", "TSLA"]

    def test_skips_readme_and_underscore_files(self, tmp_path):
        """_splits_readme.txt and other underscore files are ignored."""
        split_dir = tmp_path / "splits"
        split_dir.mkdir()
        (split_dir / "AAPL.txt").write_text("")
        (split_dir / "_splits_readme.txt").write_text("")

        result = _discover_supplementary_tickers(None, split_dir)

        assert result == ["AAPL"]

    def test_none_dirs_yield_empty(self):
        """Both dirs None → empty list."""
        assert _discover_supplementary_tickers(None, None) == []


# ---------------------------------------------------------------------------
# _print_supplementary_summary — informational, never raises
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrintSupplementarySummary:
    def test_none_is_noop(self):
        """None / empty supplementary results render nothing and don't raise."""
        _print_supplementary_summary(None)
        _print_supplementary_summary([])

    def test_renders_counts_and_failures(self):
        """A mix of success + failure renders without raising."""
        results = [
            SupplementaryLoadResult("AAPL", "success", dividend_count=4, split_count=1),
            SupplementaryLoadResult("BAD", "failed", error="boom"),
        ]
        _print_supplementary_summary(results)


# ---------------------------------------------------------------------------
# CLI flag wiring + _run_import behavioral isolation (AC-3, AC-6)
# ---------------------------------------------------------------------------


@contextmanager
def _patch_import_collaborators(bar_results, supp_loader=None):
    """Patch every collaborator _run_import constructs, yielding key mocks.

    Lets us drive _run_import end-to-end without a real DB, catalog, or
    ImportService. ``bar_results`` is what ImportService.import_directory
    returns; ``supp_loader`` (if given) replaces the SupplementaryDataLoader
    class so tests can assert construction / return values.
    """
    settings = MagicMock()
    settings.catalog.catalog_base_path = "/tmp/catalog"

    session = MagicMock()
    session_maker = MagicMock(return_value=session)

    mapper = MagicMock()
    mapper.is_loaded.return_value = True

    import_service = MagicMock()
    import_service.import_directory.return_value = bar_results

    loader_cls = supp_loader if supp_loader is not None else MagicMock()

    with (
        patch("src.config.get_settings", return_value=settings),
        patch(
            "src.db.session_sync.get_sync_session_maker",
            return_value=session_maker,
        ),
        patch("src.services.firstrate.catalog_manager.CatalogManager"),
        patch("src.services.firstrate.metadata_service.MetadataService"),
        patch(
            "src.services.firstrate.instrument_mapper.InstrumentMapper",
            return_value=mapper,
        ),
        patch(
            "src.services.firstrate.import_service.ImportService",
            return_value=import_service,
        ),
        patch("src.db.repositories.catalog_instrument_repository.SyncCatalogInstrumentRepository"),
        patch("src.db.repositories.catalog_dividend_repository.SyncCatalogDividendRepository"),
        patch("src.db.repositories.catalog_stock_split_repository.SyncCatalogStockSplitRepository"),
        patch(
            "src.services.firstrate.supplementary_loader.SupplementaryDataLoader",
            loader_cls,
        ),
    ):
        yield {
            "loader_cls": loader_cls,
            "import_service": import_service,
            "session": session,
        }


@pytest.mark.unit
class TestRunImportSupplementaryWiring:
    def test_absent_dirs_skip_supplementary_entirely(self, tmp_path):
        """No dirs given and none discovered → loader never constructed (AC-6)."""
        from src.cli.commands.import_data import _run_import

        bar_results = [ImportResult(ticker="SPY", status="success", row_count=10, duration=0.1)]
        loader_cls = MagicMock()
        with _patch_import_collaborators(bar_results, supp_loader=loader_cls):
            exit_code = _run_import("firstrate", "test", tmp_path, "stock", "daily", None, None)

        assert exit_code == 0
        loader_cls.assert_not_called()  # AC-6: zero supplementary path

    def test_supplementary_failure_does_not_flip_exit_code(self, tmp_path):
        """A supplementary failure leaves bar results + exit code untouched (AC-3)."""
        from src.cli.commands.import_data import _run_import

        div_dir = tmp_path / "stock_dividends"
        div_dir.mkdir()
        (div_dir / "SPY_divs.txt").write_text("2026-02-09,0.26\n")

        # Bar import all-success → exit code must stay 0 despite supp failure.
        bar_results = [ImportResult(ticker="SPY", status="success", row_count=10, duration=0.1)]

        loader_instance = MagicMock()
        loader_instance.load_for_tickers.return_value = [
            SupplementaryLoadResult("SPY", "failed", error="db boom")
        ]
        loader_cls = MagicMock(return_value=loader_instance)

        with _patch_import_collaborators(bar_results, supp_loader=loader_cls):
            exit_code = _run_import(
                "firstrate",
                "test",
                tmp_path,
                "stock",
                "daily",
                div_dir,
                None,
            )

        assert exit_code == 0  # AC-3: supplementary failure is non-blocking
        loader_instance.load_for_tickers.assert_called_once()
        # The ticker list was derived from the dividend dir.
        called_tickers = loader_instance.load_for_tickers.call_args.args[0]
        assert called_tickers == ["SPY"]

    def test_explicit_flag_passed_to_run_import(self, runner, tmp_path):
        """--dividends-dir/--splits-dir reach _run_import as overrides."""
        from src.cli.commands.import_data import import_firstrate

        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()

        with patch("src.cli.commands.import_data._run_import", return_value=0) as mock_run:
            runner.invoke(
                import_firstrate,
                [
                    "--format",
                    "firstrate",
                    "--catalog",
                    "test",
                    "--dividends-dir",
                    str(div_dir),
                    "--splits-dir",
                    str(split_dir),
                    str(tmp_path),
                ],
            )

        assert mock_run.called
        args = mock_run.call_args.args
        # Positional order: format, catalog, source, asset_class, timeframe,
        # dividends_dir, splits_dir.
        assert args[5] == div_dir
        assert args[6] == split_dir
