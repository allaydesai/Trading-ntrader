"""Component tests for ``import --format firstrate --asset-class fx`` wiring (FX M2, task 2.5)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.import_data import import_firstrate
from src.cli.commands.import_fx import run_fx_import
from src.models.catalog import AssetClass, ImportResult

pytestmark = pytest.mark.component

ALL_FIVE = ["1-DAY-LAST", "1-HOUR-LAST", "30-MINUTE-LAST", "5-MINUTE-LAST", "1-MINUTE-LAST"]


def _invoke(tmp_path: Path, *extra: str):
    args = ["--format", "firstrate", "--catalog", "firstrate-fx", "--asset-class"]
    return CliRunner().invoke(import_firstrate, [*args, "fx", *extra, str(tmp_path)])


class TestDispatch:
    def test_fx_routes_to_fx_runner_with_all_timeframes(self, tmp_path):
        with (
            patch("src.cli.commands.import_fx.run_fx_import", return_value=0) as run,
            patch("src.cli.commands.import_futures.run_futures_import") as futures,
            patch("src.cli.commands.import_data._run_import") as equity,
        ):
            result = _invoke(tmp_path)
        assert result.exit_code == 0, result.output
        run.assert_called_once_with("firstrate-fx", tmp_path, ALL_FIVE)
        futures.assert_not_called()
        equity.assert_not_called()

    def test_explicit_timeframe_and_exit_code_pass_through(self, tmp_path):
        with patch("src.cli.commands.import_fx.run_fx_import", return_value=1) as run:
            result = _invoke(tmp_path, "--timeframe", "daily,hourly")
        assert result.exit_code == 1
        run.assert_called_once_with("firstrate-fx", tmp_path, ["1-DAY-LAST", "1-HOUR-LAST"])

    def test_dry_run_accepts_both_fx_row_shapes(self, tmp_path):
        (tmp_path / "EURUSD_full_1day.txt").write_text("20100104,1.43,1.44546,1.42564,1.441,9\n")
        (tmp_path / "EURUSD_full_1hour.txt").write_text(
            "20100103,17:00:00,1.43,1.43359,1.42964,1.43241,1163\n"
        )
        result = _invoke(tmp_path, "--dry-run")
        assert result.exit_code == 0, result.output
        assert "Schema Mismatches" not in result.output
        assert "2010-01-03" in result.output
        assert "No data written" in result.output


class TestRunner:
    def _patched(self, tmp_path: Path, service, session_maker):
        settings = MagicMock()
        settings.catalog.catalog_base_path = str(tmp_path)
        return (
            patch("src.db.session_sync.get_sync_session_maker", return_value=session_maker),
            patch("src.config.get_settings", return_value=settings),
            patch(
                "src.services.firstrate.fx_import_service.FxImportService",
                return_value=service,
            ),
        )

    def test_mid_timeframes_commit_per_pass_and_report(self, tmp_path):
        session = MagicMock()
        service = MagicMock()
        service.import_directory.side_effect = [
            [ImportResult(ticker="EURUSD", status="success", row_count=2, duration=0.1)],
            [ImportResult(ticker="EURUSD", status="failed", error="boom", duration=0.1)],
        ]
        db, settings, svc = self._patched(tmp_path, service, lambda: session)
        with db, settings, svc:
            code = run_fx_import("firstrate-fx", tmp_path, ALL_FIVE[:2])
        assert code == 1
        assert session.commit.call_count == 2
        service.import_directory.assert_any_call(
            tmp_path, "firstrate-fx", AssetClass.FX, "1-HOUR-MID"
        )
        assert (tmp_path / "firstrate-fx").is_dir()

    def test_no_database_exits_2(self, tmp_path):
        db, settings, svc = self._patched(tmp_path, MagicMock(), None)
        with db, settings, svc:
            assert run_fx_import("firstrate-fx", tmp_path, ALL_FIVE) == 2
        assert not (tmp_path / "firstrate-fx").exists()

    def test_fatal_error_rolls_back_and_exits_2(self, tmp_path):
        session = MagicMock()
        service = MagicMock()
        service.import_directory.side_effect = RuntimeError("disk gone")
        db, settings, svc = self._patched(tmp_path, service, lambda: session)
        with db, settings, svc:
            assert run_fx_import("firstrate-fx", tmp_path, ALL_FIVE) == 2
        session.rollback.assert_called_once()
        session.close.assert_called_once()
