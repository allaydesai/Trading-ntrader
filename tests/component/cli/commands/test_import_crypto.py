"""Component tests for ``import --format firstrate --asset-class crypto`` (crypto M2, 2.5)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.import_crypto import run_crypto_import
from src.cli.commands.import_data import import_firstrate
from src.models.catalog import AssetClass, ImportResult

pytestmark = pytest.mark.component

ALL_FIVE = ["1-DAY-LAST", "1-HOUR-LAST", "30-MINUTE-LAST", "5-MINUTE-LAST", "1-MINUTE-LAST"]


def _invoke(tmp_path: Path, *extra: str):
    args = ["--format", "firstrate", "--catalog", "firstrate-crypto", "--asset-class"]
    return CliRunner().invoke(import_firstrate, [*args, "crypto", *extra, str(tmp_path)])


class TestDispatch:
    def test_crypto_routes_to_crypto_runner_with_all_timeframes(self, tmp_path):
        with (
            patch("src.cli.commands.import_crypto.run_crypto_import", return_value=0) as run,
            patch("src.cli.commands.import_fx.run_fx_import") as fx,
            patch("src.cli.commands.import_futures.run_futures_import") as futures,
            patch("src.cli.commands.import_data._run_import") as equity,
        ):
            result = _invoke(tmp_path)
        assert result.exit_code == 0, result.output
        run.assert_called_once_with("firstrate-crypto", tmp_path, ALL_FIVE)
        fx.assert_not_called()
        futures.assert_not_called()
        equity.assert_not_called()

    def test_explicit_timeframe_and_exit_code_pass_through(self, tmp_path):
        with patch("src.cli.commands.import_crypto.run_crypto_import", return_value=1) as run:
            result = _invoke(tmp_path, "--timeframe", "daily,hourly")
        assert result.exit_code == 1
        run.assert_called_once_with("firstrate-crypto", tmp_path, ["1-DAY-LAST", "1-HOUR-LAST"])

    def test_dry_run_accepts_crypto_rows_and_shows_ranges(self, tmp_path):
        (tmp_path / "BTC_full_1day.txt").write_text(
            "2013-04-01,93.25,105.9,93.25,104,11.5\n2026-05-01,78000,79000,77000,78500,14000\n"
        )
        (tmp_path / "BTC_full_1hour.txt").write_text(
            "2026-05-02 23:00:00,78000.1,78100,77900,78050,2\n"
        )
        with patch("src.cli.commands.import_crypto.run_crypto_import") as run:
            result = _invoke(tmp_path, "--dry-run")
        assert result.exit_code == 0, result.output
        run.assert_not_called()
        assert "Schema Mismatches" not in result.output
        assert "2013-04-01" in result.output and "2026-05-02" in result.output
        assert "No data written" in result.output


class TestRunner:
    def _patched(self, tmp_path: Path, service, session_maker):
        settings = MagicMock()
        settings.catalog.catalog_base_path = str(tmp_path)
        return (
            patch("src.db.session_sync.get_sync_session_maker", return_value=session_maker),
            patch("src.config.get_settings", return_value=settings),
            patch(
                "src.services.firstrate.crypto_import_service.CryptoImportService",
                return_value=service,
            ),
        )

    def test_last_timeframes_commit_per_pass_and_report(self, tmp_path):
        session = MagicMock()
        service = MagicMock()
        service.import_directory.side_effect = [
            [ImportResult(ticker="BTC", status="success", row_count=2, duration=0.1)],
            [ImportResult(ticker="BTC", status="failed", error="boom", duration=0.1)],
        ]
        db, settings, svc = self._patched(tmp_path, service, lambda: session)
        with db, settings, svc:
            code = run_crypto_import("firstrate-crypto", tmp_path, ["1-DAY-LAST", "1-HOUR-MID"])
        assert code == 1
        assert session.commit.call_count == 2
        service.import_directory.assert_any_call(
            tmp_path, "firstrate-crypto", AssetClass.CRYPTO, "1-HOUR-LAST"
        )
        assert (tmp_path / "firstrate-crypto").is_dir()

    def test_no_database_exits_2(self, tmp_path):
        db, settings, svc = self._patched(tmp_path, MagicMock(), None)
        with db, settings, svc:
            assert run_crypto_import("firstrate-crypto", tmp_path, ALL_FIVE) == 2
        assert not (tmp_path / "firstrate-crypto").exists()

    def test_fatal_error_rolls_back_and_exits_2(self, tmp_path):
        session = MagicMock()
        service = MagicMock()
        service.import_directory.side_effect = RuntimeError("disk gone")
        db, settings, svc = self._patched(tmp_path, service, lambda: session)
        with db, settings, svc:
            assert run_crypto_import("firstrate-crypto", tmp_path, ALL_FIVE) == 2
        session.rollback.assert_called_once()
        session.close.assert_called_once()
