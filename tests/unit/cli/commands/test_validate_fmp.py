"""Unit tests for the `catalog validate-fmp` CLI command.

Mocks the repo/DataCatalogService/FMPClient seams (mirrors test_metadata.py's
style) — no real DB, network, or Parquet catalog.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from sqlalchemy.exc import SQLAlchemyError

from src.cli.commands.validate_fmp import DEFAULT_TICKERS, validate_fmp
from src.config import CatalogSettings
from src.db.exceptions import DatabaseConnectionError
from src.db.models.catalog_instrument import CatalogInstrument

pytestmark = pytest.mark.unit

_EASTERN = ZoneInfo("America/New_York")


@pytest.fixture
def runner():
    return CliRunner()


def _dates(n: int, start: date = date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _bar(instrument_id: str, trade_date: date, close: float) -> Bar:
    dt_utc = datetime(
        trade_date.year, trade_date.month, trade_date.day, tzinfo=_EASTERN
    ).astimezone(timezone.utc)
    ts_ns = int(dt_utc.timestamp()) * 1_000_000_000
    bar_type = BarType.from_str(f"{instrument_id}-1-DAY-LAST-EXTERNAL")
    return Bar(
        bar_type=bar_type,
        open=Price(close, precision=2),
        high=Price(close + 1.0, precision=2),
        low=Price(close - 1.0, precision=2),
        close=Price(close, precision=2),
        volume=Quantity(1_000_000, precision=0),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _fmp_rows(dates: list[date], close: float = 100.0) -> list[dict]:
    return [{"date": d.isoformat(), "close": close} for d in dates]


def _row(ticker: str, nautilus_id: str) -> CatalogInstrument:
    return CatalogInstrument(
        ticker=ticker,
        nautilus_id=nautilus_id,
        catalog_name="firstrate-etf",
        asset_class="ETF",
        date_range_end_daily=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


def _session_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__.return_value = MagicMock()
    ctx.__exit__.return_value = False
    return ctx


class TestValidateFmpCommand:
    def test_exit_code_0_all_pass(self, runner):
        dates = _dates(200)
        row = _row("SPY", "SPY.ARCA")
        bars = [_bar("SPY.ARCA", d, 100.0) for d in dates]
        fmp_rows = _fmp_rows(dates)

        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.return_value = row
            catalog_cls.return_value.query_bars.return_value = bars
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = fmp_rows

            result = runner.invoke(validate_fmp, ["--tickers", "SPY"])

        assert result.exit_code == 0, result.output

    def test_data_catalog_service_points_at_named_catalog_directory(self, runner):
        """DataCatalogService must resolve the named catalog's on-disk directory.

        Regression test: constructing DataCatalogService() with no arguments
        silently falls back to NAUTILUS_PATH (or ./data/catalog), which is a
        DIFFERENT directory tree from the named ``firstrate-etf`` catalog —
        every ticker looks like it has zero bars, not because the data is
        missing but because the wrong catalog was queried.
        """
        row = _row("SPY", "SPY.ARCA")
        dates = _dates(200)
        bars = [_bar("SPY.ARCA", d, 100.0) for d in dates]
        fmp_rows = _fmp_rows(dates)

        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.return_value = row
            catalog_cls.return_value.query_bars.return_value = bars
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = fmp_rows

            runner.invoke(validate_fmp, ["--catalog", "firstrate-etf", "--tickers", "SPY"])

            catalog_cls.assert_called_once()
            called_path = catalog_cls.call_args.kwargs["catalog_path"]

        expected = Path(CatalogSettings().catalog_base_path) / "firstrate-etf"
        assert Path(called_path) == expected

    def test_exit_code_1_on_tolerance_breach(self, runner):
        dates = _dates(200)
        row = _row("ARKK", "ARKK.BATS")
        bars = [_bar("ARKK.BATS", d, 100.0) for d in dates]
        fmp_rows = _fmp_rows(dates, close=98.0)  # 2% off — breaches both tolerances

        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.return_value = row
            catalog_cls.return_value.query_bars.return_value = bars
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = fmp_rows

            result = runner.invoke(validate_fmp, ["--tickers", "ARKK"])

        assert result.exit_code == 1

    def test_unresolved_ticker_skipped_without_crashing_batch(self, runner):
        dates = _dates(200)
        good_row = _row("SPY", "SPY.ARCA")
        bars = [_bar("SPY.ARCA", d, 100.0) for d in dates]
        fmp_rows = _fmp_rows(dates)

        def get_by_ticker(_catalog_name, ticker):
            return good_row if ticker == "SPY" else None

        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.side_effect = get_by_ticker
            catalog_cls.return_value.query_bars.return_value = bars
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = fmp_rows

            result = runner.invoke(validate_fmp, ["--tickers", "SPY,NOPE"])

        # NOPE fails to resolve (hard fail) but SPY still gets evaluated —
        # overall exit is 1 (NOPE breach) yet both tickers appear in output.
        assert result.exit_code == 1
        assert "SPY" in result.output
        assert "NOPE" in result.output
        assert repo_cls.return_value.get_by_ticker.call_count == 2

    def test_tickers_override_restricts_run_to_subset(self, runner):
        row = _row("SPY", "SPY.ARCA")
        dates = _dates(200)
        bars = [_bar("SPY.ARCA", d, 100.0) for d in dates]
        fmp_rows = _fmp_rows(dates)

        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.return_value = row
            catalog_cls.return_value.query_bars.return_value = bars
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = fmp_rows

            runner.invoke(validate_fmp, ["--tickers", "SPY"])

            called_tickers = [
                call.args[1] for call in repo_cls.return_value.get_by_ticker.call_args_list
            ]

        assert called_tickers == ["SPY"]  # not the full DEFAULT_TICKERS 8-ticker list

    def test_default_tickers_used_when_no_override(self, runner):
        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.return_value = None  # every ticker unresolved
            catalog_cls.return_value.query_bars.return_value = []
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = []

            runner.invoke(validate_fmp, [])

            called_tickers = [
                call.args[1] for call in repo_cls.return_value.get_by_ticker.call_args_list
            ]

        assert called_tickers == list(DEFAULT_TICKERS)

    def test_db_session_failure_exits_2(self, runner):
        with patch(
            "src.cli.commands.validate_fmp.get_sync_session",
            side_effect=DatabaseConnectionError("db unreachable"),
        ):
            result = runner.invoke(validate_fmp, ["--tickers", "SPY"])

        assert result.exit_code == 2

    def test_db_session_sqlalchemy_error_exits_2(self, runner):
        with patch(
            "src.cli.commands.validate_fmp.get_sync_session",
            side_effect=SQLAlchemyError("boom"),
        ):
            result = runner.invoke(validate_fmp, ["--tickers", "SPY"])

        assert result.exit_code == 2

    def test_output_json_written_with_expected_shape(self, runner, tmp_path):
        dates = _dates(200)
        row = _row("SPY", "SPY.ARCA")
        bars = [_bar("SPY.ARCA", d, 100.0) for d in dates]
        fmp_rows = _fmp_rows(dates)
        output_path = tmp_path / "evidence.json"

        with (
            patch("src.cli.commands.validate_fmp.get_sync_session", return_value=_session_ctx()),
            patch("src.cli.commands.validate_fmp.SyncCatalogInstrumentRepository") as repo_cls,
            patch("src.cli.commands.validate_fmp.DataCatalogService") as catalog_cls,
            patch("src.cli.commands.validate_fmp.FMPClient") as fmp_cls,
        ):
            repo_cls.return_value.get_by_ticker.return_value = row
            catalog_cls.return_value.query_bars.return_value = bars
            fmp_instance = fmp_cls.return_value
            fmp_instance.__enter__.return_value = fmp_instance
            fmp_instance.fetch_historical_eod.return_value = fmp_rows

            result = runner.invoke(validate_fmp, ["--tickers", "SPY", "--output", str(output_path)])

        assert result.exit_code == 0
        assert output_path.exists()
        payload = json.loads(output_path.read_text())
        assert payload["catalog_name"]
        assert payload["overall_passed"] is True
        assert payload["tickers_passed"] == ["SPY"]
