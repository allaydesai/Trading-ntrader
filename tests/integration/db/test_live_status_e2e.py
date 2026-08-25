"""End-to-end: `live create` -> `live status` -> `live list` (Story 2.8).

Architecture names ``tests/e2e/test_live_cli.py`` for this proof, but that
basename already exists at ``tests/unit/cli/commands/test_live_cli.py`` and
the repo's basename-uniqueness rule forbids a second. The e2e tier also has
no Postgres fixture (only ``tests/e2e/test_simple_backtest.py``, which needs
none) — so this lands beside its siblings in ``tests/integration/db/``,
which already carries the real-Postgres CLI proof for `create`
(``test_cli_live_create.py``), disclosed here rather than glossed over.

Mirrors ``test_cli_live_create.py``'s idiom: patch ``get_sync_session`` with a
contextmanager yielding the shared ``sync_db_session`` fixture, at **each**
module that imports it as a module-level name — `create` lives in
``live.py``, `status`/`list` in the new ``live_status.py``.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.cli.commands.live import live


@pytest.fixture
def runner():
    return CliRunner()


def _run(runner, sync_db_session, args):
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session
        sync_db_session.commit()

    with (
        patch("src.cli.commands.live.get_sync_session", mock_get_sync_session),
        patch("src.cli.commands.live_status.get_sync_session", mock_get_sync_session),
    ):
        return runner.invoke(live, args)


@pytest.mark.integration
class TestCreateStatusListRoundTrip:
    def test_a_freshly_created_session_reports_created_and_stopped_health(
        self, runner, sync_db_session
    ):
        created = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "e2e-status-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )
        assert created.exit_code == 0, created.output

        status = _run(runner, sync_db_session, ["status", "e2e-status-session"])

        assert status.exit_code == 0, status.output
        assert "created" in status.output
        # AC #2 step 1: any non-running status derives `stopped` health.
        assert "stopped" in status.output
        assert "never" in status.output

    def test_status_json_carries_the_pinned_seven_keys(self, runner, sync_db_session):
        created = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "e2e-status-json-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )
        assert created.exit_code == 0, created.output

        status = _run(runner, sync_db_session, ["status", "e2e-status-json-session", "--json"])

        assert status.exit_code == 0, status.output
        payload = json.loads(status.output)
        assert set(payload) == {
            "session_id",
            "name",
            "status",
            "closed_trade_count",
            "open_positions",
            "last_activity_at",
            "health",
        }
        assert payload["name"] == "e2e-status-json-session"
        assert payload["status"] == "created"
        assert payload["health"] == "stopped"
        assert payload["closed_trade_count"] == 0
        assert payload["open_positions"] == 0
        assert payload["last_activity_at"] is None

    def test_status_resolves_the_same_session_by_its_session_id(self, runner, sync_db_session):
        created = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "e2e-status-by-id-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )
        assert created.exit_code == 0, created.output
        # Read the id back from the row rather than parsing console text: the
        # console log line and the Rich print both contain the literal
        # substring "session_id=", so a text split is fragile by construction.
        from sqlalchemy import select

        from src.db.models.trading_session import TradingSession

        row = sync_db_session.execute(
            select(TradingSession).where(TradingSession.name == "e2e-status-by-id-session")
        ).scalar_one()

        status = _run(runner, sync_db_session, ["status", str(row.session_id)])

        assert status.exit_code == 0, status.output
        assert "e2e-status-by-id-session" in status.output

    def test_list_renders_the_created_session(self, runner, sync_db_session):
        created = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "e2e-list-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )
        assert created.exit_code == 0, created.output

        listed = _run(runner, sync_db_session, ["list"])

        assert listed.exit_code == 0, listed.output
        assert "e2e-list-session" in listed.output

    def test_list_json_includes_the_created_session(self, runner, sync_db_session):
        created = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                "e2e-list-json-session",
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )
        assert created.exit_code == 0, created.output

        listed = _run(runner, sync_db_session, ["list", "--json"])

        assert listed.exit_code == 0, listed.output
        payload = json.loads(listed.output)
        names = {entry["name"] for entry in payload}
        assert "e2e-list-json-session" in names

    def test_status_on_an_unknown_session_exits_one(self, runner, sync_db_session):
        result = _run(runner, sync_db_session, ["status", "no-such-e2e-session"])

        assert result.exit_code == 1
        assert "live status failed" in result.output
