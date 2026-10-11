"""Unit tests for ``ntrader live seal`` (Story 5.3).

Every seam is patched at its **new-module** target —
``src.cli.commands.live_seal`` — so nothing here opens a database, Redis or a
broker. The seal itself (``seal_session``) is a spy: what it does is proven in
``tests/unit/services/test_session_seal_service.py`` and against Postgres in
``tests/integration/db/test_session_seal.py``. This file proves the command's
own contract: the option surface, the composition (commit only on success,
the engine always disposed, the session view as the open-position reader), the
report on both channels, and AR28's exit codes.
"""

import ast
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner
from structlog.testing import capture_logs

from src.cli.commands import live_seal
from src.cli.commands.live import live
from src.core.live_check import EXIT_ERROR
from src.db.exceptions import (
    DuplicateRecordError,
    InvalidSessionTransition,
    SealRefusedError,
    ValidationError,
)
from src.models.reconciliation import SessionView
from src.services.session_conditions import AR7_KEYS, build_session_conditions
from src.services.session_seal_service import (
    LINKED_BACKTEST_WARNING_EVENT,
    SealReport,
    render_seal_report,
    seal_json_payload,
)
from tests.unit.cli.commands.test_live_status_cli import (
    _ar36_violations,
    _module_scope_get_settings_calls,
)

pytestmark = pytest.mark.unit

SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
RUN_ID = UUID("44444444-4444-4444-4444-444444444444")
WHEN = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
ARGV = ["seal", "paper-alpha", "--starting-capital", "100000.00"]


def _report(**overrides) -> SealReport:
    values = dict(
        session_id=SESSION_ID,
        name="paper-alpha",
        run_id=RUN_ID,
        sealed_at=WHEN,
        closed_trade_count=3,
        trades_backfilled=3,
        initial_capital=Decimal("100000.00"),
        start_date=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        end_date=WHEN,
        # Story 5.4: the document a seal now stores — AR7's keys beside 5.2's series.
        session_conditions={
            **build_session_conditions(
                starting_capital=Decimal("100000.00"),
                closed_trade_count=3,
                open_positions=(),
                last_run_flags=None,
            ),
            "equity_series": {
                "granularity": "closed_trade",
                "basis": "per_trade_price_return",
                "closed_trades": 3,
                "excludes": ["open_positions", "reconciliation_owned_activity"],
                "curve": [{"time": 1, "value": 1.0}],
            },
        },
    )
    values.update(overrides)
    return SealReport(**values)


class _Harness:
    def __init__(self, *, report=None, error: BaseException | None = None) -> None:
        self.db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        self.seal = AsyncMock(return_value=report or _report(), side_effect=error)
        self.dispose = AsyncMock()

        @asynccontextmanager
        async def get_session():
            yield self.db

        self.patches = (
            patch.object(live_seal, "get_session", get_session),
            patch.object(live_seal, "seal_session", self.seal),
            patch.object(live_seal, "dispose_all_connections", self.dispose),
        )

    def __enter__(self) -> "_Harness":
        for active in self.patches:
            active.start()
        return self

    def __exit__(self, *exc) -> None:
        for active in reversed(self.patches):
            active.stop()


@pytest.fixture
def runner():
    return CliRunner()


class TestTheCommandSurface:
    def test_seal_is_registered_on_the_live_group(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "seal" in result.output

    def test_the_options_are_the_session_the_capital_and_json(self):
        names = {param.name for param in live_seal.seal.params}

        assert names == {"session", "starting_capital", "as_json"}

    def test_starting_capital_is_required(self, runner):
        with _Harness() as harness:
            result = runner.invoke(live, ["seal", "paper-alpha"])

        assert result.exit_code == 2
        harness.seal.assert_not_called()

    @pytest.mark.parametrize("value", ["lots", "1e", "nan", "inf", ""])
    def test_a_capital_that_is_not_a_finite_number_is_a_usage_error(self, runner, value):
        with _Harness() as harness:
            result = runner.invoke(live, ["seal", "paper-alpha", "--starting-capital", value])

        assert result.exit_code == 2
        harness.seal.assert_not_called()

    @pytest.mark.parametrize("value", ["-1", "0", "-0", "100000.001", "1e15"])
    def test_a_capital_the_record_cannot_carry_is_a_usage_error_before_any_connection(
        self, runner, value
    ):
        """PO constraint: validated once, up front — 5.1's positive/whole-cent
        rule and Nautilus's largest amount — so no database or cache is opened."""
        with _Harness() as harness:
            result = runner.invoke(live, ["seal", "paper-alpha", "--starting-capital", value])

        assert result.exit_code == 2
        assert "starting capital" in result.output
        harness.seal.assert_not_called()

    def test_the_capital_reaches_the_seal_exactly_as_given(self, runner):
        """PO ruling R1a: stored as given — no rounding, no normalisation here."""
        with _Harness() as harness:
            runner.invoke(live, ["seal", "paper-alpha", "--starting-capital", "100000.10"])

        capital = harness.seal.call_args.kwargs["starting_capital"]
        assert isinstance(capital, Decimal)
        assert str(capital) == "100000.10"

    def test_the_module_has_no_module_scope_get_settings_call(self):
        source = live_seal.__file__
        with open(source, encoding="utf-8") as handle:
            assert _module_scope_get_settings_calls(handle.read()) == []


class TestComposition:
    def test_a_successful_seal_is_committed_and_the_engine_disposed(self, runner):
        with _Harness() as harness:
            result = runner.invoke(live, ARGV)

        assert result.exit_code == 0, result.output
        harness.db.commit.assert_awaited_once()
        harness.dispose.assert_awaited_once()
        (db, identifier), kwargs = harness.seal.call_args
        assert db is harness.db
        assert identifier == "paper-alpha"
        assert kwargs["read_open_positions"] is live_seal.read_open_positions

    def test_a_refused_seal_is_never_committed_and_the_engine_still_disposed(self, runner):
        with _Harness(error=SealRefusedError("session holds AAPL.NASDAQ 10")) as harness:
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        harness.db.commit.assert_not_awaited()
        harness.dispose.assert_awaited_once()

    def test_a_failed_seal_is_rolled_back_explicitly(self, runner):
        """Code review: not left to ``AsyncSession.__aexit__``."""
        with _Harness(error=SealRefusedError("session holds AAPL.NASDAQ 10")) as harness:
            runner.invoke(live, ARGV)

        harness.db.rollback.assert_awaited_once()

    def test_a_successful_seal_is_not_rolled_back(self, runner):
        with _Harness() as harness:
            runner.invoke(live, ARGV)

        harness.db.rollback.assert_not_awaited()

    @pytest.mark.parametrize(
        "cleanup_error", [RuntimeError("pool gone"), KeyboardInterrupt()], ids=["error", "ctrl_c"]
    )
    def test_a_committed_seal_is_never_reported_as_failed(self, runner, cleanup_error):
        """Code review / PO constraint: once the commit has landed the seal is
        permanent, so a failure in the cleanup after it is a warning beside the
        report and exit 0 — never "live seal failed", which a retry would
        contradict with "already sealed"."""
        report = _report()
        with _Harness(report=report) as harness:
            harness.dispose.side_effect = cleanup_error
            result = runner.invoke(live, ARGV)

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[-len(render_seal_report(report)) :] == (
            render_seal_report(report)
        )
        assert "live seal failed" not in result.output
        assert "committed" in result.stderr

    def test_a_failure_before_the_commit_is_not_masked_by_a_failing_cleanup(self, runner):
        with _Harness(error=SealRefusedError("session holds AAPL.NASDAQ 10")) as harness:
            harness.dispose.side_effect = RuntimeError("pool gone")
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        assert "session holds AAPL.NASDAQ 10" in result.output

    def test_a_linked_backtest_warning_is_logged_once_after_the_commit(self, runner):
        report = _report(
            linked_backtest_run_id=UUID("88888888-8888-8888-8888-888888888888"),
            linked_backtest_warning="linked backtest 8888 failed",
        )
        with _Harness(report=report) as harness, capture_logs() as logs:
            runner.invoke(live, ARGV)

        warnings = [log for log in logs if log["event"] == LINKED_BACKTEST_WARNING_EVENT]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["warning"] == "linked backtest 8888 failed"
        harness.db.commit.assert_awaited_once()

    def test_no_linked_backtest_warning_is_logged_when_the_commit_fails(self, runner):
        report = _report(linked_backtest_warning="linked backtest 8888 failed")
        with _Harness(report=report) as harness, capture_logs() as logs:
            harness.db.commit.side_effect = RuntimeError("commit lost")
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        assert [log for log in logs if log["event"] == LINKED_BACKTEST_WARNING_EVENT] == []

    def test_the_open_position_reader_is_the_sessions_own_view(self):
        """PO ruling R2a: the engine cache, read-only, on the configured account."""
        view = SessionView("PAPER-33333333", (), ())
        settings = SimpleNamespace(
            ibkr=SimpleNamespace(tws_account="DU1234567"), redis=SimpleNamespace()
        )
        reader = MagicMock(return_value=view)
        with (
            patch.object(live_seal, "get_settings", return_value=settings),
            patch.object(live_seal, "read_session_view", reader),
        ):
            assert live_seal.read_open_positions(SESSION_ID) is view

        reader.assert_called_once_with(SESSION_ID, "DU1234567", settings.redis)

    @pytest.mark.parametrize("account", ["", "   "])
    def test_no_configured_account_fails_closed(self, account):
        """Without the account, positions cannot be attributed — so the view
        cannot vouch for flat, and the seal refuses rather than guessing."""
        settings = SimpleNamespace(ibkr=SimpleNamespace(tws_account=account), redis=None)
        with (
            patch.object(live_seal, "get_settings", return_value=settings),
            patch.object(live_seal, "read_session_view") as reader,
            pytest.raises(SealRefusedError, match="TWS_ACCOUNT"),
        ):
            live_seal.read_open_positions(SESSION_ID)

        reader.assert_not_called()


class TestTheReport:
    def test_the_human_report_is_the_rendered_payload(self, runner):
        report = _report()
        with _Harness(report=report):
            result = runner.invoke(live, ARGV)

        lines = render_seal_report(report)
        assert result.exit_code == 0
        # The report is the tail of stdout; a `session.sealed` log line may
        # precede it in human mode, where the console log shares stdout.
        assert result.output.splitlines()[-len(lines) :] == lines

    def test_json_stdout_is_one_document_and_nothing_else(self, runner):
        report = _report()
        with _Harness(report=report):
            result = runner.invoke(live, [*ARGV, "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == seal_json_payload(report)
        assert "\x1b[" not in result.stdout

    @pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
    def test_logs_and_prints_during_the_seal_stay_off_json_stdout(self, runner, as_json):
        """The console log handler writes to stdout (``configure_logging``), and
        the seal's dependencies log at INFO. Under ``--json`` that would break
        ``| jq``, so for the seal's duration a stdout-bound handler is pointed
        at stderr, then restored; human mode leaves it alone."""
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        root = logging.getLogger()
        root.addHandler(handler)

        async def noisy(*args, **kwargs):
            logging.getLogger("seal-probe").warning("noise from a dependency")
            print("printed noise")
            return _report()

        try:
            with _Harness() as harness:
                harness.seal.side_effect = noisy
                result = runner.invoke(live, [*ARGV, "--json"] if as_json else ARGV)
        finally:
            root.removeHandler(handler)

        assert result.exit_code == 0
        assert handler.stream is buffer
        if as_json:
            assert json.loads(result.stdout) == seal_json_payload(_report())
            assert buffer.getvalue() == ""
            assert "noise from a dependency" in result.stderr
            assert "printed noise" in result.stderr
        else:
            assert "noise from a dependency" in buffer.getvalue()
            assert "printed noise" in result.stdout

    def test_a_linked_backtest_warning_is_on_both_channels(self, runner):
        """PO ruling R4a (refined): warn, still seal — identical fields."""
        report = _report(
            linked_backtest_run_id=UUID("88888888-8888-8888-8888-888888888888"),
            linked_backtest_warning="linked backtest 8888… failed",
        )
        with _Harness(report=report):
            human = runner.invoke(live, ARGV)
            machine = runner.invoke(live, [*ARGV, "--json"])

        assert human.exit_code == machine.exit_code == 0
        assert "linked_backtest_warning: linked backtest 8888… failed" in human.output
        assert json.loads(machine.stdout)["linked_backtest_warning"] == (
            "linked backtest 8888… failed"
        )

    @pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
    def test_an_emoji_code_in_a_value_is_printed_as_typed(self, runner, as_json):
        """Code review: Rich substitutes ``:100:`` even with ``markup=False``,
        so ``aapl:100:test`` printed as ``aapl💯test`` and the JSON stopped
        matching the record."""
        report = _report(name="aapl:100:test")
        with _Harness(report=report):
            result = runner.invoke(live, [*ARGV, "--json"] if as_json else ARGV)

        assert result.exit_code == 0
        if as_json:
            assert json.loads(result.stdout)["name"] == "aapl:100:test"
        else:
            assert "name: aapl:100:test" in result.stdout

    def test_a_long_line_is_never_hard_wrapped(self, runner):
        warning = "linked backtest " + "x" * 300 + " failed"
        report = _report(linked_backtest_warning=warning)
        with _Harness(report=report):
            result = runner.invoke(live, ARGV)

        assert f"linked_backtest_warning: {warning}" in result.stdout.splitlines()

    def test_the_output_uses_no_ar36_lifecycle_synonym(self, runner):
        """AR36 bans lifecycle synonyms (close/halt/kill/pause/finalize), not
        trade vocabulary. Story 5.2's stored ``closed_trades`` key and
        ``closed_trade`` granularity are the latter — the same carve-out
        ``_ar36_violations`` already makes for "closed trade" and
        ``closed_trade_count`` — so they are scrubbed here too, never renamed."""
        with _Harness():
            result = runner.invoke(live, ARGV)

        assert "closed_trade" in result.output, "the scrub below must have something to scrub"
        assert _ar36_violations(result.output.replace("closed_trade", "")) == []

    @pytest.mark.parametrize("as_json", [False, True], ids=["human", "json"])
    def test_every_condition_and_the_last_runs_flags_print_with_no_ar36_synonym(
        self, runner, as_json
    ):
        """Story 5.4: the conditions reach the operator in both forms, the
        last run's flags included — and none of the new keys, nor a realistic
        flags document, trips AR36's lifecycle-synonym scan."""
        conditions = dict(_report().session_conditions)
        # The free-text fields too (code review P12): `detail` and the refusal
        # `reason` are copied as stored. AR36 governs this codebase's own
        # vocabulary; the scan below proves the report adds no lifecycle stem.
        # Verbatim broker/exception text is the operator's own data, recorded
        # as stored (R2a), and is not this test's subject.
        conditions["last_run_flags"] = {
            "v": 1,
            "all_failed": False,
            "failed_strategies": [
                {
                    "strategy_id": "SMACrossover-000",
                    "spec_strategy_id": "sma_crossover",
                    "error_type": "ValueError",
                    "handler": "on_bar",
                    "at": "2026-10-02T15:30:00+00:00",
                    "detail": "indicator not ready",
                }
            ],
            "order_rejections": {
                "rejected": 2,
                "denied": 0,
                "consecutive": 2,
                "first_at": "2026-10-02T15:00:00+00:00",
                "last": {
                    "at": "2026-10-02T15:10:00+00:00",
                    "kind": "rejected",
                    "reason": "Order rejected - insufficient buying power",
                },
            },
        }
        extras = {"start_date_basis", "last_run_flags"}
        argv = [*ARGV, "--json"] if as_json else ARGV
        with _Harness(report=_report(session_conditions=conditions)):
            result = runner.invoke(live, argv)

        assert result.exit_code == 0
        if as_json:
            printed = json.loads(result.stdout)["execution_conditions"]["session_conditions"]
            assert set(AR7_KEYS) | extras <= set(printed)
            assert printed["last_run_flags"] == conditions["last_run_flags"]
        else:
            labels = {line.strip().split(":", 1)[0] for line in result.stdout.splitlines()}
            assert set(AR7_KEYS) | extras <= labels
            assert "insufficient buying power" in result.stdout
        assert _ar36_violations(result.output.replace("closed_trade", "")) == []


class TestExitCodes:
    def test_the_help_documents_the_database_exit_4_collision(self, runner):
        """Code review: a refused Postgres connection is a ``ConnectionRefusedError``,
        which AR28's table maps to 4 — documented as `live status --help` does,
        never remapped (Story 2.8's AC #10)."""
        result = runner.invoke(live, ["seal", "--help"])

        assert result.exit_code == 0
        assert "4  a database failure" in result.output
        assert "Not a broker diagnosis" in result.output

    def test_a_refused_postgres_connection_exits_4(self, runner):
        with _Harness(error=ConnectionRefusedError("connect call failed")):
            result = runner.invoke(live, ARGV)

        assert result.exit_code == 4

    def test_a_refused_state_transition_exits_one_with_its_own_text(self, runner):
        error = InvalidSessionTransition("Session 'paper-alpha' cannot move from 'running'")
        with _Harness(error=error):
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        assert "live seal failed" in result.output
        assert "cannot move from 'running'" in result.output

    def test_a_seal_refusal_exits_one_with_its_own_text(self, runner):
        with _Harness(error=SealRefusedError("session holds AAPL.NASDAQ 10")):
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        assert "session holds AAPL.NASDAQ 10" in result.output

    @pytest.mark.parametrize(
        "error",
        [
            ValidationError("Invalid metric value: sharpe_ratio is NaN"),
            DuplicateRecordError("Run ID 4444 already exists"),
        ],
        ids=["nan", "duplicate"],
    )
    def test_the_persistence_services_own_failures_print_by_name(self, runner, error):
        """AR12: these propagate unchanged from the backtest path; their text
        is this codebase's own, so it is shown, prefixed with the type."""
        with _Harness(error=error):
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        assert f"{type(error).__name__}: {error}" in result.output

    def test_a_third_party_failure_is_reported_by_type_only(self, runner):
        with _Harness(error=RuntimeError("account DU1234567 leaked")):
            result = runner.invoke(live, ARGV)

        assert result.exit_code == EXIT_ERROR
        assert "DU1234567" not in result.output
        assert "RuntimeError" in result.output

    def test_json_mode_keeps_stdout_parseable_on_failure(self, runner):
        with _Harness(error=SealRefusedError("session holds AAPL.NASDAQ 10")):
            result = runner.invoke(live, [*ARGV, "--json"])

        assert result.exit_code == EXIT_ERROR
        assert result.stdout == ""
        assert "live seal failed" in result.stderr


class TestTheModuleImportsNoBroker:
    def test_no_ib_or_live_node_module_is_imported(self):
        with open(live_seal.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        assert "src.core.live_session_view" in modules
        assert not [m for m in modules if m.startswith(("ibapi", "nautilus_trader"))]
        assert not [m for m in modules if "live_session_runner" in m or "live_order" in m]
