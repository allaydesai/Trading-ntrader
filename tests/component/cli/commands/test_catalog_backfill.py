"""``ntrader catalog backfill-coverage-starts``: dry run writes nothing, a real run commits."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.catalog import catalog
from src.services.firstrate.coverage import TIMEFRAME_START_FIELDS

pytestmark = pytest.mark.component

MODULE = "src.cli.commands.catalog"
START_NS = 946_962_000_000_000_000  # 2000-01-04T05:00:00Z


def _row(**overrides):
    fields = dict(
        ticker="QQQ",
        nautilus_id="QQQ.NASDAQ",
        date_range_start=None,
        bar_count_daily=10,
        bar_count_hourly=0,
        bar_count_minute=0,
        bar_count_5min=0,
        bar_count_30min=0,
        **{name: None for name in TIMEFRAME_START_FIELDS.values()},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def wired(tmp_path):
    session = MagicMock()
    parquet = MagicMock()
    parquet.get_intervals.return_value = [(START_NS, START_NS + 1)]
    rows = [_row(), _row(ticker="ZZZ", nautilus_id=None)]

    @contextmanager
    def fake_session():
        yield session

    with (
        patch(f"{MODULE}.get_sync_session", fake_session),
        patch(f"{MODULE}.CatalogSettings", return_value=MagicMock(catalog_base_path=tmp_path)),
        patch(f"{MODULE}.CatalogManager") as manager,
        patch(f"{MODULE}.SyncCatalogInstrumentRepository") as repo,
    ):
        manager.return_value.resolve_catalog.return_value = parquet
        repo.return_value.iter_by_catalog.return_value = iter(rows)
        yield SimpleNamespace(session=session, parquet=parquet, rows=rows, manager=manager)


def _invoke(*args):
    return CliRunner().invoke(catalog, ["backfill-coverage-starts", *args])


def test_a_dry_run_rolls_back_and_reports_what_would_change(wired):
    result = _invoke("--catalog", "firstrate-etf", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "1 would change" in result.output
    assert "1 unqualified" in result.output
    wired.session.rollback.assert_called_once()
    wired.session.commit.assert_not_called()


def test_a_real_run_commits_starts_read_from_the_file_names(wired):
    result = _invoke("--catalog", "firstrate-etf")

    assert result.exit_code == 0, result.output
    wired.session.commit.assert_called_once()
    expected = datetime(2000, 1, 4, 5, 0, tzinfo=timezone.utc)
    assert wired.rows[0].date_range_start_daily == expected
    assert wired.rows[0].date_range_start == expected
    identifier = wired.parquet.get_intervals.call_args.args[1]
    assert identifier == "QQQ.NASDAQ-1-DAY-LAST-EXTERNAL"


def test_an_unknown_catalog_is_an_error_not_a_traceback(wired):
    wired.manager.return_value.resolve_catalog.side_effect = FileNotFoundError("no such catalog")

    result = _invoke("--catalog", "nope")

    assert result.exit_code != 0
    assert "no such catalog" in result.output
    wired.session.commit.assert_not_called()
