"""Component tests for ``data import --asset-class futures`` wiring (futures M2)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.import_data import import_firstrate
from src.cli.commands.import_futures import ensure_catalog_dir, run_futures_import
from src.models.catalog import AssetClass, ImportResult

pytestmark = pytest.mark.component

ALL_FIVE = ["1-DAY-LAST", "1-HOUR-LAST", "30-MINUTE-LAST", "5-MINUTE-LAST", "1-MINUTE-LAST"]


def _invoke(tmp_path: Path, *extra: str):
    args = ["--format", "firstrate", "--catalog", "firstrate-futures", "--asset-class"]
    return CliRunner().invoke(import_firstrate, [*args, "futures", *extra, str(tmp_path)])


class TestDispatch:
    def test_futures_routes_to_futures_runner_with_all_timeframes(self, tmp_path):
        with (
            patch("src.cli.commands.import_futures.run_futures_import", return_value=0) as run,
            patch("src.cli.commands.import_data._run_import") as equity,
        ):
            result = _invoke(tmp_path)
        assert result.exit_code == 0, result.output
        run.assert_called_once_with("firstrate-futures", tmp_path, ALL_FIVE)
        equity.assert_not_called()

    def test_explicit_timeframe_and_exit_code_pass_through(self, tmp_path):
        with patch("src.cli.commands.import_futures.run_futures_import", return_value=1) as run:
            result = _invoke(tmp_path, "--timeframe", "daily,hourly")
        assert result.exit_code == 1
        run.assert_called_once_with("firstrate-futures", tmp_path, ["1-DAY-LAST", "1-HOUR-LAST"])

    def test_dry_run_accepts_seven_column_daily(self, tmp_path):
        (tmp_path / "ES_full_1day_continuous_ratio_adjusted.txt").write_text(
            "2026-04-30,7150.0,7253.5,7133.75,7243.75,1732392,1968318\n"
        )
        result = _invoke(tmp_path, "--dry-run")
        assert result.exit_code == 0, result.output
        assert "Schema Mismatches" not in result.output
        assert "No data written" in result.output


class TestRunner:
    def test_ensure_catalog_dir_creates_once(self, tmp_path):
        path = ensure_catalog_dir(tmp_path, "firstrate-futures")
        assert path.is_dir()
        assert ensure_catalog_dir(tmp_path, "firstrate-futures") == path

    def test_commits_after_each_timeframe_and_reports(self, tmp_path):
        session = MagicMock()
        service = MagicMock()
        service.import_directory.side_effect = [
            [ImportResult(ticker="ES", status="success", row_count=2, duration=0.1)],
            [ImportResult(ticker="ES", status="failed", error="boom", duration=0.1)],
        ]
        settings = MagicMock()
        settings.catalog.catalog_base_path = str(tmp_path)
        with (
            patch("src.db.session_sync.get_sync_session_maker", return_value=lambda: session),
            patch("src.config.get_settings", return_value=settings),
            patch(
                "src.services.firstrate.futures_import_service.FuturesImportService",
                return_value=service,
            ),
        ):
            code = run_futures_import("firstrate-futures", tmp_path, ALL_FIVE[:2])
        assert code == 1
        assert session.commit.call_count == 2
        service.import_directory.assert_any_call(
            tmp_path, "firstrate-futures", AssetClass.FUTURES, "1-HOUR-LAST"
        )
        assert (tmp_path / "firstrate-futures").is_dir()

    def test_no_database_is_exit_2(self, tmp_path):
        with patch("src.db.session_sync.get_sync_session_maker", return_value=None):
            assert run_futures_import("firstrate-futures", tmp_path, ALL_FIVE) == 2
