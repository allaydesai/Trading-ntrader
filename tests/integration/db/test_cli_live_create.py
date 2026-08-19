"""Integration tests for the `ntrader live create` CLI command (Story 2.2).

Real Postgres write path — the unit-tier ``test_live_cli.py`` covers the option
surface and usage-error paths with a mocked repository; this file covers AC #5,
#6, #9, #12 against a real database. Mirrors ``test_cli_show.py``'s idiom:
patch ``get_sync_session`` with a contextmanager yielding the fixture session,
since ``live.py`` imports it as a module-level name.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from src.cli.commands.live import live
from src.db.models.trading_session import TradingSession
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.models.session import SessionSpec, SessionStatus


@pytest.fixture
def runner():
    return CliRunner()


def _run(runner, sync_db_session, args):
    @contextmanager
    def mock_get_sync_session():
        # Mirrors the real get_sync_session's commit-on-clean-exit behaviour:
        # if the CLI raises (e.g. SystemExit on a duplicate name), the
        # exception interrupts the generator before this line runs.
        yield sync_db_session
        sync_db_session.commit()

    with patch("src.cli.commands.live.get_sync_session", mock_get_sync_session):
        return runner.invoke(live, args)


@pytest.mark.integration
class TestLiveCreateWritesARealSession:
    def test_create_persists_a_row_with_status_created(self, runner, sync_db_session):
        result = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "integration-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )

        assert result.exit_code == 0, result.output

        row = sync_db_session.execute(
            select(TradingSession).where(TradingSession.name == "integration-session")
        ).scalar_one()
        assert row.status == SessionStatus.CREATED
        assert row.session_id is not None
        assert str(row.session_id) in result.output

    def test_created_spec_round_trips_losslessly_with_decimal(self, runner, sync_db_session):
        result = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "roundtrip-cli-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "--param",
                "fast_period=12",
            ],
        )
        assert result.exit_code == 0, result.output

        row = sync_db_session.execute(
            select(TradingSession).where(TradingSession.name == "roundtrip-cli-session")
        ).scalar_one()
        restored = SessionSpec.from_stored(row.spec)

        assert restored.strategies[0].strategy_id == "sma_crossover"
        assert restored.strategies[0].parameters["fast_period"] == 12
        assert type(restored.strategies[0].parameters["portfolio_value"]) is Decimal

    def test_duplicate_name_exits_one_and_creates_no_second_row(self, runner, sync_db_session):
        first_args = [
            "create",
            "--name",
            "dup-cli-session",
            "--strategy",
            "sma_crossover",
            "--bar-type",
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        ]
        first = _run(runner, sync_db_session, first_args)
        assert first.exit_code == 0, first.output

        second = _run(runner, sync_db_session, first_args)

        assert second.exit_code == 1
        assert "dup-cli-session" in second.output

        # The mock `get_sync_session` (unlike the real one) does not roll back
        # on the way out, so the failed flush leaves the fixture's shared
        # session's transaction aborted — roll back before reusing it, exactly
        # as the real `get_sync_session` would via `Session.close()`.
        sync_db_session.rollback()

        count = (
            sync_db_session.execute(
                select(TradingSession).where(TradingSession.name == "dup-cli-session")
            )
            .scalars()
            .all()
        )
        assert len(count) == 1

    def test_compare_to_links_an_existing_backtest_run(self, runner, sync_db_session):
        backtest_repo = SyncBacktestRepository(sync_db_session)
        run = backtest_repo.create_backtest_run(
            run_id=uuid4(),
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("1.0"),
            config_snapshot={"version": "1.0"},
        )
        sync_db_session.commit()

        result = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "compare-cli-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "--compare-to",
                str(run.run_id),
            ],
        )

        assert result.exit_code == 0, result.output
        row = sync_db_session.execute(
            select(TradingSession).where(TradingSession.name == "compare-cli-session")
        ).scalar_one()
        assert row.linked_backtest_run_id == run.run_id

    def test_unknown_compare_to_exits_one_with_no_foreign_key_traceback(
        self, runner, sync_db_session
    ):
        unknown_run_id = uuid4()

        result = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "unknown-compare-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "--compare-to",
                str(unknown_run_id),
            ],
        )

        assert result.exit_code == 1
        assert "IntegrityError" not in result.output
        assert str(unknown_run_id) in result.output

        rows = (
            sync_db_session.execute(
                select(TradingSession).where(TradingSession.name == "unknown-compare-session")
            )
            .scalars()
            .all()
        )
        assert rows == []
