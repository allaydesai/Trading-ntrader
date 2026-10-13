"""Unit tests for ``ntrader live reconcile <session>`` (Story 4.6, AC #1/#4/#5).

The CLI is the composition root: it resolves the session through
``SessionService`` (read-only), hands the driver plain values plus the
comparison port, prints the report, and exits with AR28's table plus Story
4.6's ``5``. ``get_sync_session``/``SyncTradingSessionRepository`` and the
driver are patched at this module's targets, so nothing here opens a database
connection or a socket; ``SessionService`` runs for real over the stand-in
repository.
"""

import ast
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from src.cli.commands import live_reconcile as reconcile_module
from src.cli.commands.live import live
from src.core.live_broker_state import BrokerStateFailure, BrokerStateUnavailableError
from src.core.live_gate import GateRefusal, GateRefusalReason
from src.core.live_node_builder import GateRefusedError
from src.core.live_reconcile import ReconcileTarget
from src.core.live_session_view import SessionViewFailure, SessionViewUnavailableError
from src.models.reconciliation import CashLine, PositionLine, ReconciliationReport
from src.models.session import SessionStatus
from src.services import reconciliation_service

pytestmark = pytest.mark.unit

_GET_SYNC_SESSION = "src.cli.commands.live_reconcile.get_sync_session"
_SESSION_REPO = "src.cli.commands.live_reconcile.SyncTradingSessionRepository"
_DRIVER = "src.cli.commands.live_reconcile.run_reconcile"

SESSION_ID = UUID("0e8f1c2a-1111-2222-3333-444455556666")
AT = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
_READ_ONLY_REPO_METHODS = frozenset({"find_by_name", "find_by_session_id"})


@pytest.fixture
def runner():
    return CliRunner()


def _report(*, clean: bool = True) -> ReconciliationReport:
    broker = Decimal("10") if clean else Decimal("14")
    return ReconciliationReport(
        session_name="swing-1",
        trader_id="PAPER-0e8f1c2a",
        account="***626",
        positions=(PositionLine("NVDA.NASDAQ", Decimal("10"), broker, Decimal("123.4")),),
        cash=(CashLine("USD", Decimal("1000.00"), Decimal("1000.00")),),
        broker_retrieved_at=AT,
        elapsed_ms=812.0,
    )


@contextmanager
def _fake_session_cm():
    yield MagicMock()


def _row(status: SessionStatus = SessionStatus.STOPPED):
    row = MagicMock()
    row.id = 42
    row.session_id = SESSION_ID
    row.name = "swing-1"
    row.status = status
    return row


@contextmanager
def _harness(*, row=None, found: bool = True, driver_result=None, driver_raises=None):
    repo = MagicMock()
    repo.find_by_name.return_value = (row or _row()) if found else None
    repo.find_by_session_id.return_value = None
    with (
        patch(_GET_SYNC_SESSION, _fake_session_cm),
        patch(_SESSION_REPO, MagicMock(return_value=repo)),
        patch(_DRIVER) as driver,
    ):
        if driver_raises is not None:
            driver.side_effect = driver_raises
        else:
            driver.return_value = driver_result or _report()
        yield {"repo": repo, "driver": driver}


class TestRegistration:
    def test_live_help_lists_reconcile(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "reconcile" in result.output

    def test_it_takes_exactly_a_positional_session(self):
        import click

        assert [p.name for p in reconcile_module.reconcile.params] == ["session"]
        assert isinstance(reconcile_module.reconcile.params[0], click.Argument)


class TestACleanCheck:
    def test_prints_the_report_and_exits_zero(self, runner):
        with _harness() as h:
            result = runner.invoke(live, ["reconcile", "swing-1"])

        assert result.exit_code == 0, result.output
        assert "RESULT: clean" in result.output
        assert "position NVDA.NASDAQ session=+10 broker=+10" in result.output
        h["driver"].assert_called_once()

    def test_the_driver_gets_the_resolved_session_and_the_comparison_port(self, runner):
        with _harness() as h:
            runner.invoke(live, ["reconcile", "swing-1"])

        args, kwargs = h["driver"].call_args
        target = args[2]
        assert target == ReconcileTarget(name="swing-1", session_id=SESSION_ID, status="stopped")
        assert kwargs["compare"] is reconciliation_service.compare
        assert "cli_flags" not in kwargs

    def test_the_session_is_resolved_by_uuid_too(self, runner):
        with _harness(found=False) as h:
            # Only the UUID path can succeed: the name lookup finds nothing.
            h["repo"].find_by_session_id.return_value = _row()
            result = runner.invoke(live, ["reconcile", str(SESSION_ID)])

        assert result.exit_code == 0, result.output
        h["repo"].find_by_session_id.assert_called_once_with(SESSION_ID)
        h["driver"].assert_called_once()

    def test_the_header_names_the_session_and_the_reconcile_client_id(self, runner):
        with _harness():
            result = runner.invoke(live, ["reconcile", "swing-1"])

        assert "swing-1" in result.output
        assert "status=stopped" in result.output
        assert "client_id=" in result.output

    def test_a_running_session_is_warned_about_in_flight_fills(self, runner):
        with _harness(row=_row(SessionStatus.RUNNING)):
            result = runner.invoke(live, ["reconcile", "swing-1"])

        assert "running" in result.output
        assert "re-run" in result.output


class TestADiscrepancy:
    def test_reports_and_exits_five(self, runner):
        with _harness(driver_result=_report(clean=False)):
            result = runner.invoke(live, ["reconcile", "swing-1"])

        assert result.exit_code == 5, result.output
        assert "DISCREPANCY" in result.output
        assert "RESULT: discrepancy" in result.output
        assert "nothing was changed" in result.output


class TestFailures:
    @pytest.mark.parametrize(
        ("failure", "code"),
        [
            (
                GateRefusedError(
                    GateRefusal(reason=GateRefusalReason.NON_PAPER_PORT, message="port 7496")
                ),
                3,
            ),
            (BrokerStateUnavailableError(BrokerStateFailure.TIMEOUT, "no answer"), 4),
            (SessionViewUnavailableError(SessionViewFailure.NO_ENGINE_STATE, "never ran"), 1),
            (KeyboardInterrupt(), 1),
        ],
    )
    def test_a_failure_is_named_and_mapped(self, runner, failure, code):
        with _harness(driver_raises=failure):
            result = runner.invoke(live, ["reconcile", "swing-1"])

        assert result.exit_code == code, result.output
        assert "live reconcile failed:" in result.output
        assert "RESULT: clean" not in result.output

    def test_an_unknown_session_exits_one_before_the_driver(self, runner):
        with _harness(found=False) as h:
            result = runner.invoke(live, ["reconcile", "nope"])

        assert result.exit_code == 1
        assert "No trading session matches" in result.output
        h["driver"].assert_not_called()


class TestNothingIsWritten:
    def test_the_repository_sees_reads_only(self, runner):
        with _harness(driver_result=_report(clean=False)) as h:
            runner.invoke(live, ["reconcile", "swing-1"])

        called = {name for name, _, _ in h["repo"].method_calls}
        assert called <= _READ_ONLY_REPO_METHODS, called - _READ_ONLY_REPO_METHODS


class TestNoRealMoneySurface:
    """The reconcile command can never declare a real-money crossing."""

    def test_no_real_money_option(self):
        names = {param.name for param in reconcile_module.reconcile.params}
        opts = {opt for p in reconcile_module.reconcile.params for opt in getattr(p, "opts", [])}
        assert "real_money" not in names
        assert "--real-money" not in opts

    def test_the_module_declares_no_gate_flags(self):
        source = Path(reconcile_module.__file__).read_text(encoding="utf-8")

        assert "run_reconcile(" in source, "the scan is not looking at the command"
        assert _real_money_surface(source) == set()

    @pytest.mark.parametrize(
        "planted",
        [
            "GateFlags(real_money=True)",
            "run_reconcile(s, r, t, cli_flags=flags)",
            "f(real_money=True)",
        ],
    )
    def test_the_scan_can_fail(self, planted):
        """Non-vacuity twin: each way of smuggling consent in is caught."""
        assert _real_money_surface(planted) != set()


def _real_money_surface(source: str) -> set[str]:
    """``GateFlags(...)`` calls and ``real_money=``/``cli_flags=`` keywords."""
    tree = ast.parse(source)
    found = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GateFlags"
    }
    found |= {
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in ("real_money", "cli_flags")
    }
    return found


#: Every module Story 4.6 added: none may call an order- or position-mutating
#: method (AC #4 / D-I). Shares the stop-path scan's constant and helper.
RECONCILE_MODULES = (
    "src/cli/commands/live_reconcile.py",
    "src/core/live_reconcile.py",
    "src/core/live_session_view.py",
    "src/services/reconciliation_service.py",
    "src/models/reconciliation.py",
)


class TestTheReconcileModulesSubmitNothing:
    """AC #4 / D-I: nothing in the reconcile path can submit, cancel, modify or close."""

    @pytest.mark.parametrize("relative_path", RECONCILE_MODULES)
    def test_no_forbidden_order_method_is_called(self, relative_path):
        from tests.unit.core.test_live_stop_path_is_inert import (
            FORBIDDEN_ORDER_METHODS,
            PROJECT_ROOT,
            _called_names,
        )

        called = _called_names((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))

        assert called, f"the scan found no call at all in {relative_path}"
        assert called & FORBIDDEN_ORDER_METHODS == set()

    def test_the_scan_can_fail(self):
        from tests.unit.core.test_live_stop_path_is_inert import (
            FORBIDDEN_ORDER_METHODS,
            _called_names,
        )

        planted = "node.trader.submit_order(order)\nself.close_position(p)\n"
        assert _called_names(planted) & FORBIDDEN_ORDER_METHODS == {
            "submit_order",
            "close_position",
        }
