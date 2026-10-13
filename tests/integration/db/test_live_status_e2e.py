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


@pytest.mark.integration
class TestRejectionsAreVisibleAcrossProcesses:
    """Story 3.7, AC #3d — the write path and the read path, both real.

    The unit tier proves the document's *rules* against a detached row; this
    proves they survive a real ``JSONB`` round trip. That distinction is
    load-bearing for one mutation in particular (M9, decision D-D's Hazard 3):
    SQLAlchemy does not track in-place mutation of a plain ``JSONB`` column,
    so a write that mutates instead of reassigning passes every MagicMock
    test and never reaches Postgres. Only this tier can tell.
    """

    @staticmethod
    def _running_session(sync_db_session, name):
        """Create a session and take it to ``running`` through the service."""
        from src.db.repositories.trading_session_repository_sync import (
            SyncTradingSessionRepository,
        )
        from src.models.session import SessionStatus
        from src.services.session_service import SessionService

        repository = SyncTradingSessionRepository(sync_db_session)
        service = SessionService(repository)
        row = repository.find_by_name(name)
        # The epoch is read back from the row the transition returns, never
        # computed as `before + 1`: `_apply_transition` increments it in
        # place on the same object, so a pre-read plus one overshoots by one
        # and the ownership guard then accepts the *wrong* epoch as current.
        running = service.transition(row.session_id, to=SessionStatus.RUNNING)
        sync_db_session.commit()
        return service, running.session_id, running.owner_epoch

    @staticmethod
    def _rejections(**overrides):
        from datetime import datetime, timezone

        fields = {
            "rejected": 1,
            "denied": 0,
            "consecutive": 1,
            "first_at": datetime(2026, 9, 22, 14, 3, 11, tzinfo=timezone.utc),
            "last_at": datetime(2026, 9, 22, 14, 3, 11, tzinfo=timezone.utc),
            "last_kind": "rejected",
            "last_client_order_id": "O-20260922-140311-0a1b2c3d-000-1",
            "last_instrument_id": "NVDA.NASDAQ",
            "last_strategy_id": "SMACrossover-000",
            "last_reason": "Order rejected - reason: insufficient margin",
            "last_reconciliation": False,
        }
        fields.update(overrides)
        return fields

    def _create(self, runner, sync_db_session, name):
        created = _run(
            runner,
            sync_db_session,
            [
                "create",
                "--name",
                name,
                "--strategy",
                "sma_crossover",
                "--bar-type",
                "NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ],
        )
        assert created.exit_code == 0, created.output

    def test_two_writes_then_status_reads_degraded_and_the_reason(self, runner, sync_db_session):
        name = "e2e-rejections-session"
        self._create(runner, sync_db_session, name)
        service, session_id, epoch = self._running_session(sync_db_session, name)

        service.record_order_rejections(session_id, owner_epoch=epoch, **self._rejections())
        sync_db_session.commit()
        service.record_order_rejections(
            session_id,
            owner_epoch=epoch,
            **self._rejections(
                rejected=2,
                consecutive=2,
                last_client_order_id="O-20260922-140905-deadbeef-000-3",
            ),
        )
        sync_db_session.commit()

        status = _run(runner, sync_db_session, ["status", name])

        assert status.exit_code == 0, status.output
        assert "degraded" in status.output
        assert "insufficient margin" in status.output
        assert "NVDA.NASDAQ" in status.output
        assert "O-20260922-140905-deadbeef-000-3" in status.output
        # The snapshot REPLACED its predecessor: the first order's id is gone.
        assert "O-20260922-140311-0a1b2c3d-000-1" not in status.output

    def test_the_second_write_actually_reached_the_column(self, runner, sync_db_session):
        """Mutation M9's home. Against a detached row, mutating the nested
        dict in place looks identical to reassigning it; against Postgres it
        is the difference between a persisted fact and a silent no-op.
        """
        from sqlalchemy import select

        from src.db.models.trading_session import TradingSession

        name = "e2e-rejections-persisted-session"
        self._create(runner, sync_db_session, name)
        service, session_id, epoch = self._running_session(sync_db_session, name)

        service.record_order_rejections(session_id, owner_epoch=epoch, **self._rejections())
        sync_db_session.commit()
        service.record_order_rejections(
            session_id, owner_epoch=epoch, **self._rejections(rejected=7, consecutive=7)
        )
        sync_db_session.commit()
        sync_db_session.expire_all()

        row = sync_db_session.execute(
            select(TradingSession).where(TradingSession.name == name)
        ).scalar_one()

        document = row.runtime_flags["order_rejections"]
        assert document["rejected"] == 7
        assert document["consecutive"] == 7
        assert row.runtime_flags["v"] == 1

    def test_status_json_keeps_its_seven_keys_with_health_degraded(self, runner, sync_db_session):
        name = "e2e-rejections-json-session"
        self._create(runner, sync_db_session, name)
        service, session_id, epoch = self._running_session(sync_db_session, name)

        service.record_order_rejections(
            session_id, owner_epoch=epoch, **self._rejections(rejected=2, consecutive=2)
        )
        sync_db_session.commit()

        status = _run(runner, sync_db_session, ["status", name, "--json"])

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
        assert payload["health"] == "degraded"

    def test_the_block_survives_a_stop(self, runner, sync_db_session):
        """``runtime_flags`` is cleared only on the next ``-> running`` edge,
        so an operator investigating a session that stopped trading still has
        the evidence at the moment they need it.
        """
        from src.models.session import SessionStatus

        name = "e2e-rejections-stopped-session"
        self._create(runner, sync_db_session, name)
        service, session_id, epoch = self._running_session(sync_db_session, name)
        service.record_order_rejections(
            session_id, owner_epoch=epoch, **self._rejections(rejected=2, consecutive=2)
        )
        sync_db_session.commit()

        service.transition(session_id, to=SessionStatus.STOPPED, owner_epoch=epoch)
        sync_db_session.commit()

        status = _run(runner, sync_db_session, ["status", name])

        assert status.exit_code == 0, status.output
        assert "insufficient margin" in status.output
        # `stopped` outranks `degraded` in the precedence, and that is correct:
        # the session is not impaired, it is over.
        assert "health: stopped" in status.output

    def test_a_write_against_a_stopped_row_is_refused(self, runner, sync_db_session):
        """The run the summary would describe is over."""
        from src.db.exceptions import InvalidSessionTransition
        from src.models.session import SessionStatus

        name = "e2e-rejections-refused-session"
        self._create(runner, sync_db_session, name)
        service, session_id, epoch = self._running_session(sync_db_session, name)
        service.transition(session_id, to=SessionStatus.STOPPED, owner_epoch=epoch)
        sync_db_session.commit()

        with pytest.raises(InvalidSessionTransition):
            service.record_order_rejections(session_id, owner_epoch=epoch, **self._rejections())

    def test_a_foreign_owner_epoch_is_refused(self, runner, sync_db_session):
        """A dispossessed incumbent must not stamp its refusals into its
        successor's row (Story 3.6, retrospective D1's fencing token).
        """
        from src.db.exceptions import InvalidSessionTransition

        name = "e2e-rejections-fenced-session"
        self._create(runner, sync_db_session, name)
        service, session_id, epoch = self._running_session(sync_db_session, name)

        with pytest.raises(InvalidSessionTransition, match="reclaimed"):
            service.record_order_rejections(session_id, owner_epoch=epoch - 1, **self._rejections())
