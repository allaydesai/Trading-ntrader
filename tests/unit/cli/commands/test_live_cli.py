"""Unit tests for the `ntrader live check` CLI command (Story 1.7).

Every test patches ``run_live_check``, so nothing here constructs a
``TradingNode``, opens a socket or initialises the Nautilus C logging subsystem —
which is what keeps the file in the unit tier despite the transitive Nautilus
import. Precedent for that placement: ``test_validate_fmp.py`` imports
``nautilus_trader.model.data`` under the same marker.

What is under test is the exit-code contract (AR28/FR11) and the option surface,
not the driver — the driver has its own component suite.
"""

import re
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from click.testing import CliRunner

from src.cli.commands.live import (
    DEFAULT_BAR_TYPE,
    MAX_SESSION_NAME_LENGTH,
    check,
    create,
    live,
)
from src.cli.main import cli
from src.core.live_check import EXIT_ERROR, LiveCheckOutcome, LiveCheckReport
from src.core.live_gate import GateMode, GateRefusalReason
from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError

pytestmark = pytest.mark.unit

_DRIVER = "src.cli.commands.live.run_live_check"
_GET_SYNC_SESSION = "src.cli.commands.live.get_sync_session"
_SESSION_REPO = "src.cli.commands.live.SyncTradingSessionRepository"
_BACKTEST_REPO = "src.cli.commands.live.SyncBacktestRepository"

# `start`'s `claim_session`/`exit_with`/`release_quietly` moved to
# `live_start.py` (Story 2.6, file-size budget) — these patch that module,
# not `live.py`, even though the ``create`` command above patches the same
# names on `live.py` itself.
_START_GET_SYNC_SESSION = "src.cli.commands.live_start.get_sync_session"
_START_SESSION_REPO = "src.cli.commands.live_start.SyncTradingSessionRepository"


@pytest.fixture
def runner():
    return CliRunner()


def _report(outcome: LiveCheckOutcome, **overrides) -> LiveCheckReport:
    fields = {"outcome": outcome, "message": "a message for the operator"}
    fields.update(overrides)
    return LiveCheckReport(**fields)  # type: ignore[arg-type]


class TestGroupRegistration:
    """AC #5 — the group is wired in and lists its command."""

    def test_the_live_group_is_registered_on_the_root_cli(self, runner):
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "live" in result.output

    def test_live_help_lists_check(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "check" in result.output

    def test_check_help_lists_its_options(self, runner):
        result = runner.invoke(live, ["check", "--help"])

        assert result.exit_code == 0
        for option in ("--bar-type", "--observe-seconds", "--connect-timeout", "--require-bars"):
            assert option in result.output


class TestConsoleOutputIsConsistentWithTheOtherGroups:
    """AC #4 — structured structlog console output, wired the same way (FR48).

    ``live`` gets its logging from the CLI root, exactly as ``backtest``,
    ``data`` and the rest do — it configures nothing of its own. Asserted by
    identity rather than by reading source: a second, divergent ``configure_logging``
    is precisely the drift this guards against, and a text match would not see it.
    The events themselves are asserted in the driver's component suite, where a
    real check can be run against doubles.
    """

    def test_the_root_cli_configures_structlog_for_every_group(self):
        import src.cli.main as main
        import src.utils.logging as logging_utils

        assert main.configure_logging is logging_utils.configure_logging

    def test_the_live_group_installs_no_logging_of_its_own(self):
        import src.cli.commands.live as live_module

        assert not hasattr(live_module, "configure_logging"), (
            "the live group configures logging itself; it must inherit the root's "
            "configuration so its output matches the other command groups"
        )

    def test_every_logging_live_module_binds_a_structlog_logger(self):
        """Not ``print`` or a bare ``logging.getLogger`` — the stream is structured."""
        import logging

        import structlog

        import src.core.live_account_gate as account_gate
        import src.core.live_bar_observer as bar_observer
        import src.core.live_check as check
        import src.core.live_check_driver as driver

        reference = structlog.get_logger("reference")
        for module in (driver, check, account_gate, bar_observer):
            logger = module.logger
            assert logger is not None, f"{module.__name__} has no logger"
            # Same type structlog hands out here, so a swap to `logging.getLogger`
            # or a hand-rolled shim fails rather than passing on duck-typing.
            assert type(logger) is type(reference), (
                f"{module.__name__}.logger is not a structlog logger ({type(logger)!r})"
            )
            assert not isinstance(logger, logging.Logger)

    def test_the_gate_itself_stays_out_of_the_logging_stack(self):
        """Story 1.1's purity AC, restated from this side: the gate imports no logger.

        Worth pinning next to the tests above, because "make the live modules log
        consistently" is exactly the change that would reach for a logger in
        ``live_gate`` and break its no-I/O guarantee. The refusal is logged by
        ``live_check``, which is where the structured ``gate.static`` event comes from.
        """
        import src.core.live_gate as gate

        assert not hasattr(gate, "logger")
        assert "structlog" not in gate.__dict__


class TestExitCodes:
    """AR28's table, as the operator's scripts will observe it."""

    def test_a_successful_check_exits_zero(self, runner):
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK, mode=GateMode.PAPER)):
            result = runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert result.exit_code == 0
        assert "ok" in result.output

    def test_a_gate_refusal_exits_three(self, runner):
        refusal = _report(
            LiveCheckOutcome.GATE_REFUSED,
            message="IBKR_PORT 4001 is not a known paper port (4002, 7497).",
            refusal_reason=GateRefusalReason.NON_PAPER_PORT,
        )
        with patch(_DRIVER, return_value=refusal):
            result = runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert result.exit_code == 3

    def test_a_gate_refusal_prints_the_reason_and_the_message(self, runner):
        refusal = _report(
            LiveCheckOutcome.GATE_REFUSED,
            message="IBKR_PORT 4001 is not a known paper port (4002, 7497).",
            refusal_reason=GateRefusalReason.NON_PAPER_PORT,
        )
        with patch(_DRIVER, return_value=refusal):
            result = runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert GateRefusalReason.NON_PAPER_PORT.value in result.output
        assert "not a known paper port" in result.output

    def test_an_unreachable_broker_exits_four(self, runner):
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.BROKER_UNREACHABLE)):
            result = runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert result.exit_code == 4

    def test_three_and_four_are_the_scriptable_distinction(self, runner):
        """FR11 in one assertion: a script can tell refusal from failure."""
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.GATE_REFUSED)):
            refused = runner.invoke(live, ["check", "--observe-seconds", "0"]).exit_code
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.BROKER_UNREACHABLE)):
            unreachable = runner.invoke(live, ["check", "--observe-seconds", "0"]).exit_code

        assert (refused, unreachable) == (3, 4)

    @pytest.mark.parametrize(
        "outcome",
        [LiveCheckOutcome.CONFIG_ERROR, LiveCheckOutcome.INTERRUPTED, LiveCheckOutcome.ERROR],
    )
    def test_other_failures_exit_one(self, runner, outcome):
        with patch(_DRIVER, return_value=_report(outcome)):
            result = runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert result.exit_code == 1


class TestUsageErrors:
    """AR28 reserves 2 for Click's usage errors; keep it meaning that."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["check", "--observe-seconds", "-1"],
            ["check", "--connect-timeout", "0"],
            ["check", "--connect-timeout", "-5"],
            ["check", "--nonsense"],
        ],
    )
    def test_bad_input_exits_two_without_reaching_the_driver(self, runner, argv):
        with patch(_DRIVER) as driver:
            result = runner.invoke(live, argv)

        assert result.exit_code == 2
        driver.assert_not_called()


class TestOptionsReachTheDriver:
    """What the operator typed is what the check runs."""

    def test_the_default_bar_type_is_used_when_none_is_given(self, runner):
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK)) as driver:
            runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert tuple(driver.call_args.kwargs["bar_types"]) == (DEFAULT_BAR_TYPE,)

    def test_repeated_bar_type_options_all_arrive(self, runner):
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK)) as driver:
            runner.invoke(
                live,
                [
                    "check",
                    "--observe-seconds",
                    "0",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--bar-type",
                    "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert tuple(driver.call_args.kwargs["bar_types"]) == (
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        )

    def test_require_bars_and_the_two_timings_arrive(self, runner):
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK)) as driver:
            runner.invoke(
                live,
                [
                    "check",
                    "--observe-seconds",
                    "12.5",
                    "--connect-timeout",
                    "7",
                    "--require-bars",
                ],
            )

        kwargs = driver.call_args.kwargs
        assert kwargs["observe_seconds"] == 12.5
        assert kwargs["connect_timeout"] == 7.0
        assert kwargs["require_bars"] is True

    def test_require_bars_defaults_off(self, runner):
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK)) as driver:
            runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert driver.call_args.kwargs["require_bars"] is False

    def test_settings_are_passed_positionally_not_fetched_by_the_driver(self, runner):
        """The CLI is the composition root; `src/core/live_*` never calls get_settings()."""
        from src.config import IBKRSettings

        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK)) as driver:
            runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert isinstance(driver.call_args.args[0], IBKRSettings)


class TestNoRealMoneySurface:
    """AC #6 — the check can never declare a real-money crossing."""

    def test_the_command_has_no_real_money_option(self):
        names = {param.name for param in check.params}
        assert "real_money" not in names
        opts = {opt for param in check.params for opt in getattr(param, "opts", [])}
        assert "--real-money" not in opts

    def test_the_command_declares_no_gate_flags_at_all(self):
        """The only way to smuggle consent in is to construct `GateFlags` here.

        Asserted against the module's *code*, not its prose — the docstring says
        at length why the flag is absent, and a grep for the flag's spelling
        would fail on the explanation. Anyone wiring a crossing would have to
        build a `GateFlags` in this module, which is what this catches.
        """
        import ast
        from pathlib import Path

        from src.cli.commands import live as live_module

        tree = ast.parse(Path(live_module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "GateFlags" not in called

        assigned_keywords = {
            keyword.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
        }
        assert "real_money" not in assigned_keywords
        assert "cli_flags" not in assigned_keywords

    def test_no_cli_flags_are_declared_on_the_drivers_behalf(self, runner):
        """`None` means 'no declaration was made' — the gate's safe default."""
        with patch(_DRIVER, return_value=_report(LiveCheckOutcome.OK)) as driver:
            runner.invoke(live, ["check", "--observe-seconds", "0"])

        assert driver.call_args.kwargs.get("cli_flags") is None


def _fake_session_cm():
    """A ``get_sync_session``-shaped context manager yielding a bare MagicMock."""

    @contextmanager
    def _cm():
        yield MagicMock()

    return _cm


class TestCreateCommandRegistration:
    """AC #5 — `create` is wired into the `live` group."""

    def test_live_help_lists_create(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "create" in result.output

    def test_create_help_lists_its_options(self, runner):
        result = runner.invoke(live, ["create", "--help"])

        assert result.exit_code == 0
        for option in ("--name", "--strategy", "--bar-type", "--param", "--compare-to"):
            assert option in result.output

    def test_create_has_no_json_option(self):
        """Architecture D8 scopes --json to status/list (Story 2.8), not create."""
        opts = {opt for param in create.params for opt in getattr(param, "opts", [])}
        assert "--json" not in opts

    def test_name_length_limit_matches_the_column_width(self):
        """The CLI guard and `trading_sessions.name` must not drift apart.

        If the column ever widens, a CLI still rejecting at 100 silently caps a
        capability the schema allows; if the column narrows, the CLI lets a name
        through that Postgres then truncates at exit 1.
        """
        from src.db.models.trading_session import TradingSession

        assert MAX_SESSION_NAME_LENGTH == TradingSession.__table__.columns["name"].type.length


class TestCreateUsageErrors:
    """AR28 reserves exit 2 for everything Click can express as misuse."""

    def test_unknown_strategy_exits_two_and_lists_registered_names(self, runner):
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "not_a_real_strategy",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == 2
        assert "not_a_real_strategy" in result.output
        assert "sma_crossover" in result.output

    def test_missing_bar_type_exits_two(self, runner):
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(live, ["create", "--name", "s1", "--strategy", "sma_crossover"])

        assert result.exit_code == 2

    def test_malformed_param_missing_equals_exits_two(self, runner):
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--param",
                    "fast_period",
                ],
            )

        assert result.exit_code == 2

    def test_duplicate_param_key_exits_two(self, runner):
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--param",
                    "fast_period=5",
                    "--param",
                    "fast_period=6",
                ],
            )

        assert result.exit_code == 2

    @pytest.mark.parametrize("bad_name", ["", "   ", "\t", "x" * 101])
    def test_blank_or_overlong_name_exits_two(self, runner, bad_name):
        """A blank name becomes a permanent, unaddressable handle; >100 chars
        would otherwise reach Postgres and surface as a raw truncation error at
        exit 1, contradicting this command's own exit-code table.
        """
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    bad_name,
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == 2, result.output

    def test_a_name_of_exactly_the_column_width_is_accepted(self, runner):
        """The boundary is 100, not 99 — String(100) holds exactly 100."""
        instance = MagicMock()
        instance.create.return_value = MagicMock(session_id=uuid4())
        repo_cls = MagicMock(return_value=instance)

        with patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "x" * 100,
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == 0, result.output

    def test_name_is_not_silently_trimmed(self, runner):
        """A spec frozen for a session's life must store what the operator typed."""
        instance = MagicMock()
        instance.create.return_value = MagicMock(session_id=uuid4())
        repo_cls = MagicMock(return_value=instance)

        with patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    " padded name ",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == 0, result.output
        assert instance.create.call_args.kwargs["name"] == " padded name "

    @pytest.mark.parametrize("bad_param", ["=12", "   =12"])
    def test_blank_param_key_exits_two_and_names_the_offending_text(self, runner, bad_param):
        """An empty key would otherwise render as `Unknown parameter(s): .`"""
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--param",
                    bad_param,
                ],
            )

        assert result.exit_code == 2
        assert repr(bad_param) in result.output or bad_param.strip() in result.output

    def test_unknown_param_key_is_quoted_so_whitespace_is_visible(self, runner):
        """` fast_period` must not render indistinguishably from `fast_period`."""
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--param",
                    " fast_period=5",
                ],
            )

        assert result.exit_code == 2
        assert "' fast_period'" in result.output

    def test_unknown_param_key_exits_two_and_lists_valid_ones(self, runner):
        """AC #11 — the typo'd-key guard this story exists to close."""
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--param",
                    "fast_perios=12",
                ],
            )

        assert result.exit_code == 2
        assert "fast_perios" in result.output
        assert "fast_period" in result.output

    def test_invalid_parameter_value_exits_two(self, runner):
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--param",
                    "fast_period=not-a-number",
                ],
            )

        assert result.exit_code == 2

    def test_unusable_bar_type_exits_two(self, runner):
        with patch(_GET_SYNC_SESSION, _fake_session_cm()):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "garbage",
                ],
            )

        assert result.exit_code == 2


class TestCreateHappyPath:
    """AC #5, #6 — a session row is written and the session id/name are printed."""

    def _invoke(self, runner, repo_cls, backtest_repo_cls=None, extra_args=None):
        args = [
            "create",
            "--name",
            "s1",
            "--strategy",
            "sma_crossover",
            "--bar-type",
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        ] + (extra_args or [])
        patches = [patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls)]
        if backtest_repo_cls is not None:
            patches.append(patch(_BACKTEST_REPO, backtest_repo_cls))
        for p in patches:
            p.start()
        try:
            return runner.invoke(live, args)
        finally:
            for p in patches:
                p.stop()

    def test_successful_creation_exits_zero_and_prints_session_id(self, runner):
        fake_session_id = uuid4()
        instance = MagicMock()
        instance.create.return_value = MagicMock(session_id=fake_session_id)
        repo_cls = MagicMock(return_value=instance)

        result = self._invoke(runner, repo_cls)

        assert result.exit_code == 0, result.output
        assert str(fake_session_id) in result.output
        assert "s1" in result.output

    def test_successful_creation_passes_the_serialised_spec_and_name(self, runner):
        instance = MagicMock()
        instance.create.return_value = MagicMock(session_id=uuid4())
        repo_cls = MagicMock(return_value=instance)

        self._invoke(runner, repo_cls)

        kwargs = instance.create.call_args.kwargs
        assert kwargs["name"] == "s1"
        assert isinstance(kwargs["spec"], dict)
        assert kwargs["linked_backtest_run_id"] is None

    def test_name_is_escaped_before_printing(self, runner):
        instance = MagicMock()
        instance.create.return_value = MagicMock(session_id=uuid4())
        repo_cls = MagicMock(return_value=instance)

        args = [
            "create",
            "--name",
            "weird[markup]",
            "--strategy",
            "sma_crossover",
            "--bar-type",
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        ]
        with patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls):
            result = runner.invoke(live, args)

        assert result.exit_code == 0, result.output
        # The exit code alone cannot fail: rich does not raise on an unknown
        # tag, it silently swallows it. Only the round-tripped text proves
        # escape() ran.
        assert "weird[markup]" in result.output

    def test_a_name_containing_a_valid_style_token_survives_printing(self, runner):
        """`[red]` is a *valid* rich style, so an unescaped name loses it silently."""
        instance = MagicMock()
        instance.create.return_value = MagicMock(session_id=uuid4())
        repo_cls = MagicMock(return_value=instance)

        args = [
            "create",
            "--name",
            "a[red]b",
            "--strategy",
            "sma_crossover",
            "--bar-type",
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        ]
        with patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls):
            result = runner.invoke(live, args)

        assert result.exit_code == 0, result.output
        assert "a[red]b" in result.output

    def test_compare_to_resolves_and_is_passed_through(self, runner):
        run_id = uuid4()
        session_instance = MagicMock()
        session_instance.create.return_value = MagicMock(session_id=uuid4())
        session_repo_cls = MagicMock(return_value=session_instance)

        backtest_instance = MagicMock()
        backtest_instance.find_by_run_id.return_value = MagicMock(run_id=run_id)
        backtest_repo_cls = MagicMock(return_value=backtest_instance)

        result = self._invoke(
            runner,
            session_repo_cls,
            backtest_repo_cls,
            extra_args=["--compare-to", str(run_id)],
        )

        assert result.exit_code == 0, result.output
        assert session_instance.create.call_args.kwargs["linked_backtest_run_id"] == run_id

    def test_session_created_is_logged(self, runner):
        """AR41's event shape: event name positional, context as kwargs."""
        instance = MagicMock()
        fake_id = uuid4()
        instance.create.return_value = MagicMock(session_id=fake_id)
        repo_cls = MagicMock(return_value=instance)

        with patch("src.cli.commands.live.logger") as mock_logger:
            self._invoke(runner, repo_cls)

        mock_logger.info.assert_called_once_with(
            "session.created", session_id=str(fake_id), name="s1"
        )


class TestCreateStateConflicts:
    """AR28 reserves exit 1 for a state conflict or DB failure, not usage errors."""

    def test_duplicate_name_exits_one(self, runner):
        instance = MagicMock()
        instance.create.side_effect = DuplicateRecordError("Session name 's1' already exists")
        repo_cls = MagicMock(return_value=instance)

        with patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == EXIT_ERROR
        assert "already exists" in result.output

    def test_unknown_compare_to_exits_one(self, runner):
        session_instance = MagicMock()
        session_repo_cls = MagicMock(return_value=session_instance)

        backtest_instance = MagicMock()
        backtest_instance.find_by_run_id.return_value = None
        backtest_repo_cls = MagicMock(return_value=backtest_instance)

        run_id = uuid4()
        with (
            patch(_GET_SYNC_SESSION, _fake_session_cm()),
            patch(_SESSION_REPO, session_repo_cls),
            patch(_BACKTEST_REPO, backtest_repo_cls),
        ):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "--compare-to",
                    str(run_id),
                ],
            )

        assert result.exit_code == EXIT_ERROR
        assert str(run_id) in result.output
        session_instance.create.assert_not_called()

    def test_database_unavailable_exits_one(self, runner):
        def _raise_runtime_error():
            raise RuntimeError("Database not configured. Check DATABASE_URL in .env file")

        with patch(_GET_SYNC_SESSION, side_effect=_raise_runtime_error):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == EXIT_ERROR

    def test_database_connection_error_exits_one(self, runner):
        instance = MagicMock()
        instance.create.side_effect = DatabaseConnectionError("Database connection failed")
        repo_cls = MagicMock(return_value=instance)

        with patch(_GET_SYNC_SESSION, _fake_session_cm()), patch(_SESSION_REPO, repo_cls):
            result = runner.invoke(
                live,
                [
                    "create",
                    "--name",
                    "s1",
                    "--strategy",
                    "sma_crossover",
                    "--bar-type",
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                ],
            )

        assert result.exit_code == EXIT_ERROR


_RUNNER = "src.cli.commands.live.LiveSessionRunner"
_RECORD = "src.cli.commands.live.SqlSessionRecord"
_SERVICE = "src.cli.commands.live_start.SessionService"
_CACHE = "src.cli.commands.live.build_cache_config"


def _trading_session_row(**overrides):
    """A ``TradingSession``-shaped stand-in with everything ``start`` reads."""
    from datetime import datetime, timezone
    from uuid import UUID

    row = MagicMock()
    row.session_id = overrides.get("session_id", UUID("44444444-4444-4444-4444-444444444444"))
    row.name = overrides.get("name", "alpha-session")
    row.spec = overrides.get("spec", {"schema_version": 1, "strategies": []})
    row.last_started_at = overrides.get(
        "last_started_at", datetime(2026, 8, 19, 15, 0, 0, tzinfo=timezone.utc)
    )
    return row


@contextmanager
def _start_harness(
    *,
    row=None,
    resolve_error=None,
    transition_error=None,
    runner_error=None,
    construct_error=None,
):
    """Patch every collaborator ``live start`` composes, and hand back the spies."""
    row = _trading_session_row() if row is None else row
    service = MagicMock()
    service.resolve.side_effect = resolve_error
    if resolve_error is None:
        service.resolve.return_value = row
    service.transition.side_effect = transition_error
    if transition_error is None:
        service.transition.return_value = row

    runner = MagicMock()
    runner.run.side_effect = runner_error
    # A clean stop's shape: a signal ended it and the release wrote fine —
    # explicit rather than a MagicMock's own truthy auto-attributes, so the
    # clean-stop console text this harness's callers do not assert on today
    # does not silently start containing a `<MagicMock ...>` repr.
    runner.stop_signal = "SIGINT"
    runner.record_release_failed = False
    # Review fix, 2026-08-22 (decision D4): both are explicit for the same
    # reason as the two above — a bare MagicMock attribute is truthy, so an
    # unset `shutdown_problems` would make every clean-stop test print the
    # teardown warning with a `<MagicMock ...>` repr inside it.
    runner.shutdown_problems = []
    runner.stopped_by_signal = True
    # Story 2.7, and explicit for the third time for the same reason: a bare
    # MagicMock attribute is truthy AND iterable-looking, so an unset
    # `contained_failures` would make every clean-stop test print the
    # contained-strategy warning with a `<MagicMock ...>` repr inside it.
    runner.contained_failures = ()
    # And a fourth time: a truthy `all_strategies_failed` would flip every
    # contained-failure test into the every-strategy-failed branch.
    runner.all_strategies_failed = False
    record = MagicMock()

    with (
        patch(_START_GET_SYNC_SESSION, _fake_session_cm()),
        patch(_START_SESSION_REPO),
        patch(_SERVICE, return_value=service),
        patch(_RECORD, return_value=record) as record_cls,
        patch(_CACHE, return_value="a-cache-config"),
        patch("src.cli.commands.live.SessionSpec") as spec_cls,
        patch(_RUNNER, side_effect=construct_error, return_value=runner) as runner_cls,
    ):
        yield {
            "service": service,
            "spec_cls": spec_cls,
            "runner": runner,
            "runner_cls": runner_cls,
            "record": record,
            "record_cls": record_cls,
            "row": row,
        }


class TestStartCommandRegistration:
    """AC #1 — ``start`` exists, and re-specifies nothing (FR16)."""

    def test_live_help_lists_start(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "start" in result.output

    def test_start_takes_exactly_a_session_and_a_connect_timeout(self):
        """An **exact** set, so any added option fails here and forces a
        deliberate decision. A three-name negative assertion (``"strategy" not
        in names``) would be satisfied by ``--fast-period``.
        """
        from src.cli.commands.live import start

        assert {param.name for param in start.params} == {"session", "connect_timeout"}

    def test_the_session_is_a_positional_argument(self):
        import click

        from src.cli.commands.live import start

        positional = [p for p in start.params if isinstance(p, click.Argument)]
        assert [p.name for p in positional] == ["session"]

    def test_start_has_no_json_option(self):
        """Architecture D8 scopes ``--json`` to ``status``/``list`` (Story 2.8)."""
        from src.cli.commands.live import start

        opts = {opt for param in start.params for opt in getattr(param, "opts", [])}
        assert "--json" not in opts

    def test_the_connect_timeout_default_is_the_runners_not_the_checks(self):
        """The two wait for different post-conditions and must not share a number."""
        from src.cli.commands.live import DEFAULT_CONNECT_TIMEOUT_SECONDS, start
        from src.core.live_session_runner import DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS

        default = next(p for p in start.params if p.name == "connect_timeout").default

        assert default == DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS
        assert default != DEFAULT_CONNECT_TIMEOUT_SECONDS


class TestStartComposesTheRunner:
    """``start`` is the composition root: it builds what the runner may not."""

    def test_it_resolves_the_identifier_and_transitions_to_running(self, runner):
        from src.models.session import SessionStatus

        with _start_harness() as spies:
            runner.invoke(live, ["start", "alpha-session"])

        spies["service"].resolve.assert_called_once_with("alpha-session")
        spies["service"].transition.assert_called_once_with(
            spies["row"].session_id, to=SessionStatus.RUNNING
        )

    def test_the_runner_is_constructed_with_the_stored_spec(self, runner):
        """FR16: the *stored* spec, never anything the command line supplied."""
        with _start_harness() as spies:
            runner.invoke(live, ["start", "alpha-session"])
            spies["spec_cls"].from_stored.assert_called_once_with(spies["row"].spec)

    def test_the_runner_is_given_a_redis_backed_cache_config(self, runner):
        """A ``cache=None`` session silently runs on an in-memory Nautilus cache
        and throws away the whole of Story 2.4 — AR10's per-session namespace
        and FR19's "a restarted process rejoins its own state" — with no error.
        """
        with _start_harness() as spies:
            runner.invoke(live, ["start", "alpha-session"])

        assert spies["runner_cls"].call_args.kwargs["cache"] == "a-cache-config"

    def test_the_cache_config_is_built_from_redis_settings(self, runner):
        from src.config import RedisSettings

        with _start_harness():
            with patch(_CACHE, return_value="a-cache-config") as build_cache:
                runner.invoke(live, ["start", "alpha-session"])

        assert isinstance(build_cache.call_args.args[0], RedisSettings)

    def test_the_runner_receives_ibkr_settings_not_the_whole_settings_object(self, runner):
        from src.config import IBKRSettings

        with _start_harness() as spies:
            runner.invoke(live, ["start", "alpha-session"])

        assert isinstance(spies["runner_cls"].call_args.args[0], IBKRSettings)

    def test_the_record_is_bound_to_the_row_and_the_transitions_own_instant(self, runner):
        """So the runner can never write to the wrong row, and cannot forge its
        own claim to ownership — ``started_at`` is what the reclaim guard reads.
        """
        with _start_harness() as spies:
            runner.invoke(live, ["start", "alpha-session"])

        assert spies["record_cls"].call_args.args[0] == spies["row"].session_id
        assert spies["record_cls"].call_args.kwargs["started_at"] == (spies["row"].last_started_at)

    def test_the_connect_timeout_reaches_the_runner(self, runner):
        with _start_harness() as spies:
            runner.invoke(live, ["start", "alpha-session", "--connect-timeout", "45"])

        assert spies["runner_cls"].call_args.kwargs["connect_timeout"] == 45.0

    def test_a_clean_run_exits_zero(self, runner):
        with _start_harness():
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 0


class TestStopOutput:
    """Story 2.6, Task 10 — a graceful stop's console text and exit code."""

    def test_a_stop_exits_zero_and_prints_the_stop_line(self, runner):
        with _start_harness() as spies:
            spies["runner"].stop_signal = "SIGINT"
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 0
        assert "Session stopped: alpha-session (SIGINT)" in result.output

    def test_the_stop_message_names_the_story_31_residual(self, runner):
        """Positions may have been closed by the strategy's own ``on_stop()``
        until Story 3.1 lands — the operator must not read a clean stop as
        proof positions survived it (Story 2.6's AC #2 ⚠️).
        """
        with _start_harness():
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "sma_crossover.on_stop()" in result.output
        assert "Story 3.1" in result.output

    def test_a_keyboard_interrupt_escaping_run_is_unchanged_at_exit_one(self, runner):
        """The table still has to be honest: a graceful stop raises nothing,
        so no new exit-code mapping is added for it — only the pre-existing
        ``KeyboardInterrupt`` arm, unchanged.
        """
        with _start_harness(runner_error=KeyboardInterrupt()):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == EXIT_ERROR

    def test_a_failed_release_prints_the_operator_warning(self, runner):
        """AC #9 — a structlog ERROR alone is not a report an operator
        watching the terminal ever sees; exit code stays 0.
        """
        with _start_harness() as spies:
            spies["runner"].record_release_failed = True
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 0
        assert "could not be marked stopped" in result.output
        # Review fix, 2026-08-22: `assert "90" in result.output` was satisfied
        # by any two adjacent digits anywhere in the output. Assert the actual
        # operator-relevant fact — the staleness window, with its unit.
        flat = " ".join(result.output.split())
        assert "90s" in flat
        assert "still reads `running`" in flat

    def test_a_clean_release_prints_no_warning(self, runner):
        with _start_harness() as spies:
            spies["runner"].record_release_failed = False
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "could not be marked stopped" not in result.output

    def test_no_signal_name_omits_the_parenthetical(self, runner):
        """A stop noticed at a phase boundary before any node existed still
        carries a signal name in production (Task 2/3's design) — but the
        rendering itself must not assume one, so ``None`` is exercised too.
        """
        with _start_harness() as spies:
            spies["runner"].stop_signal = None
            spies["runner"].stopped_by_signal = False
            result = runner.invoke(live, ["start", "alpha-session"])

        # Review fix, 2026-08-22: the old `A in output or output.startswith(A)`
        # was unfalsifiable with respect to the suffix it claimed to test —
        # the "(SIGINT)" rendering satisfies the `startswith` arm too. Assert
        # the rendered line exactly.
        assert "Session stopped: alpha-session\n" in result.output
        assert "(None)" not in result.output


class TestTheStopWasNotRequested:
    """Decision D4 — `run()` returning is not proof a signal ended it."""

    def test_a_stop_with_no_signal_warns_that_the_session_ended_on_its_own(self, runner):
        """``run_async`` swallows cancellation, so a node that died on its own
        also returns cleanly. Printing the same reassuring line for both made a
        crash indistinguishable from a deliberate Ctrl-C.
        """
        with _start_harness() as spies:
            spies["runner"].stop_signal = None
            spies["runner"].stopped_by_signal = False
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split())
        assert result.exit_code == 0
        assert "No stop signal was received" in flat
        assert "ended on its own" in flat

    def test_a_signalled_stop_prints_no_such_warning(self, runner):
        with _start_harness() as spies:
            spies["runner"].stopped_by_signal = True
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "No stop signal was received" not in result.output


class TestTeardownProblemsAreVisible:
    """Decision D4 — a teardown that did not complete must not read as clean."""

    def test_shutdown_problems_are_printed_and_the_exit_code_stays_zero(self, runner):
        with _start_harness() as spies:
            spies["runner"].shutdown_problems = ["stop: RuntimeError", "dispose: TimeoutError"]
            result = runner.invoke(live, ["start", "alpha-session"])

        # Rich hard-wraps at the console width, so a phrase can be split across
        # lines mid-word. Collapse whitespace before asserting on content.
        flat = " ".join(result.output.split())
        assert result.exit_code == 0, "AR28 has no code for this; Story 1.7 forbids inventing one"
        assert "teardown did not complete cleanly" in flat
        assert "stop: RuntimeError" in flat
        assert "dispose: TimeoutError" in flat
        assert "live client id" in flat

    def test_a_clean_teardown_prints_no_warning(self, runner):
        with _start_harness() as spies:
            spies["runner"].shutdown_problems = []
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "teardown did not complete cleanly" not in result.output


class TestContainedStrategyFailuresAreVisible:
    """Story 2.7, Task 10 — an operator watching a stop is told which
    strategies stopped trading, and when.

    The in-process half of the visibility ``runtime_flags`` gives across
    processes. Exit code stays **0**: the session ran and it stopped. AR28's
    table has no code for "a strategy failed", and Story 1.7 recorded that a
    CLI inventing a code outside its own documented table is worse than one
    reporting a generic failure.
    """

    @staticmethod
    def _failure(spec_strategy_id="sma_crossover", **overrides):
        from datetime import datetime, timezone

        from src.core.live_strategy_guard import StrategyFailure

        fields = {
            "strategy_id": "SMACrossover-000",
            "spec_strategy_id": spec_strategy_id,
            "error_type": "DivisionByZero",
            "handler": "handle_bar",
            "at": datetime(2026, 8, 23, 14, 3, 11, tzinfo=timezone.utc),
            "detail": "[<class 'decimal.DivisionByZero'>]",
        }
        fields.update(overrides)
        return StrategyFailure(**fields)

    def test_a_contained_failure_is_named_and_the_exit_code_stays_zero(self, runner):
        with _start_harness() as spies:
            spies["runner"].contained_failures = (self._failure(),)
            result = runner.invoke(live, ["start", "alpha-session"])

        # Rich hard-wraps at the console width, so a phrase can be split across
        # lines mid-word. Collapse whitespace before asserting on content.
        flat = " ".join(result.output.split())
        assert result.exit_code == 0
        assert "sma_crossover" in flat
        assert "SMACrossover-000" in flat
        assert "DivisionByZero" in flat
        assert "handle_bar" in flat

    def test_the_operator_is_told_where_the_traceback_is(self, runner):
        with _start_harness() as spies:
            spies["runner"].contained_failures = (self._failure(),)
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "strategy.failed" in " ".join(result.output.split())

    def test_two_failures_are_both_named(self, runner):
        with _start_harness() as spies:
            spies["runner"].contained_failures = (
                self._failure(),
                self._failure("momentum", strategy_id="SMAMomentum-001"),
            )
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split())
        assert "sma_crossover" in flat
        assert "momentum" in flat
        assert "2 strategies" in flat

    def test_one_failure_reads_as_singular(self, runner):
        with _start_harness() as spies:
            spies["runner"].contained_failures = (self._failure(),)
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split())
        assert "1 strategy was contained" in flat

    def test_a_clean_run_prints_nothing_extra(self, runner):
        with _start_harness() as spies:
            spies["runner"].contained_failures = ()
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "contained" not in result.output
        assert result.exit_code == 0

    def test_contained_failures_are_printed_even_when_the_run_ends_by_raising(self, runner):
        """Review fix, 2026-08-23: ``_print_contained_failures`` was reached
        only on a normal return. A run that ends by raising — a reclaim above
        all — may be exactly the run whose failures never reached
        ``runtime_flags`` (the write is refused once the row leaves
        ``running``), so this report is the operator's only in-process trace.
        """
        with _start_harness(runner_error=RuntimeError("the node died")) as spies:
            spies["runner"].contained_failures = (self._failure(),)
            spies["runner"].all_strategies_failed = False
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split())
        assert result.exit_code != 0
        assert "sma_crossover" in flat
        assert "contained" in flat

    def test_a_partial_failure_says_the_unaffected_strategies_kept_running(self, runner):
        with _start_harness() as spies:
            spies["runner"].contained_failures = (self._failure(),)
            spies["runner"].all_strategies_failed = False
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split())
        assert "not named above were unaffected" in flat

    def test_when_every_strategy_failed_the_cli_does_not_claim_survivors(self, runner):
        """Review fix, 2026-08-23: the old unconditional trailer said "the
        other strategies were unaffected" even when every strategy failed —
        and for a single-strategy session, where there are no other
        strategies at all. A safety report must not assert a falsehood in its
        most common trigger.
        """
        with _start_harness() as spies:
            spies["runner"].contained_failures = (self._failure(),)
            spies["runner"].all_strategies_failed = True
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split())
        assert "Every strategy in this session was contained" in flat
        assert "unaffected" not in flat
        assert "can no longer trade" in flat
        # Still exit 0: the session ran and it stopped (AR28 has no code for this).
        assert result.exit_code == 0

    @pytest.mark.parametrize("all_failed", [False, True])
    def test_the_wording_respects_ar36s_vocabulary(self, runner, all_failed):
        """``tests/unit/core/test_live_stop_path_is_inert.py`` word-boundary
        matches ``pause|halt|kill|close|finalize`` against operator-facing
        strings in the runner and the signal policy. This text lives in the CLI,
        which that scan does not cover — so the rule is asserted here directly,
        with the **same** five-stem word-boundary list the canonical scan uses
        (review fix, 2026-08-23: this used to check three past-tense words,
        omitting ``close`` — the one word AR36's list already lost silently
        once — so "positions were closed" would have passed). Both trailer
        branches are covered.
        """
        with _start_harness() as spies:
            spies["runner"].contained_failures = (self._failure(),)
            spies["runner"].all_strategies_failed = all_failed
            result = runner.invoke(live, ["start", "alpha-session"])

        flat = " ".join(result.output.split()).lower()
        contained_line = [
            part for part in flat.split("⚠️") if "contained" in part and "sma_crossover" in part
        ]
        assert contained_line, flat
        for forbidden in ("pause", "halt", "kill", "close", "finalize"):
            hits = re.findall(rf"\b{forbidden}\w*\b", contained_line[0])
            assert not hits, f"AR36 forbidden vocabulary {hits} in: {contained_line[0]}"


class TestStartExitCodes:
    """AR28's table, and the messages that make each code actionable."""

    def test_a_gate_refusal_exits_three(self, runner):
        from src.core.live_gate import GateRefusalReason, build_refusal
        from src.core.live_node_builder import GateRefusedError

        refusal = build_refusal(GateRefusalReason.NON_PAPER_PORT, "port 7496 is not paper")
        with _start_harness(runner_error=GateRefusedError(refusal.refusal)):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 3

    def test_an_unreachable_broker_exits_four(self, runner):
        from src.core.live_check import BrokerUnreachableError

        with _start_harness(runner_error=BrokerUnreachableError("gateway silent")):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 4

    @pytest.mark.parametrize(
        "exception_factory",
        [
            lambda: __import__(
                "src.core.live_cache", fromlist=["RedisUnreachableError"]
            ).RedisUnreachableError("Cannot use the engine cache's Redis at h:1 — down"),
            lambda: __import__(
                "src.db.exceptions", fromlist=["InvalidSessionTransition"]
            ).InvalidSessionTransition("Session 'alpha' is already running"),
            lambda: __import__(
                "src.core.live_node_builder", fromlist=["LiveNodeConfigError"]
            ).LiveNodeConfigError("trader_id is empty"),
            lambda: __import__(
                "src.core.live_market_data", fromlist=["LiveMarketDataError"]
            ).LiveMarketDataError("bar type is unusable"),
        ],
    )
    def test_the_other_typed_failures_exit_one(self, runner, exception_factory):
        with _start_harness(runner_error=exception_factory()):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1

    def test_an_unknown_session_exits_one_and_names_the_identifier(self, runner):
        from src.db.exceptions import RecordNotFoundError

        with _start_harness(resolve_error=RecordNotFoundError("No trading session matches 'zz'")):
            result = runner.invoke(live, ["start", "zz"])

        assert result.exit_code == 1
        assert "zz" in result.output

    def test_a_redis_failure_prints_its_own_actionable_message(self, runner):
        """host/port/remedy — suppressed to a bare type name without the entry
        in ``_SAFE_MESSAGE_EXCEPTION_NAMES``.
        """
        from src.core.live_cache import RedisUnreachableError

        message = "Cannot use the engine cache's Redis at 127.0.0.1:6399 — connection refused"
        with _start_harness(runner_error=RedisUnreachableError(message)):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert "6399" in result.output

    def test_a_state_conflict_prints_the_session_name_and_the_heartbeat_age(self, runner):
        from src.db.exceptions import InvalidSessionTransition

        message = "Session 'alpha-session' is already running and its heartbeat is 4s old"
        with _start_harness(transition_error=InvalidSessionTransition(message)):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1
        assert "alpha-session" in result.output
        assert "4s old" in result.output


class TestStartNeverRetries:
    """``deferred-work.md:920-929``: *"an ``except BacktestStorageError:
    retry()`` would spin until the incumbent's heartbeat went stale and then
    reclaim a live session"* — two processes on one broker account, the
    catastrophic failure NFR6 exists to prevent.
    """

    def test_a_state_conflict_produces_exactly_one_attempt(self, runner):
        from src.db.exceptions import InvalidSessionTransition

        with _start_harness(transition_error=InvalidSessionTransition("already running")) as spies:
            runner.invoke(live, ["start", "alpha-session"])

        assert spies["service"].transition.call_count == 1

    def test_a_failed_run_is_never_re_run(self, runner):
        from src.core.live_check import BrokerUnreachableError

        with _start_harness(runner_error=BrokerUnreachableError("silent")) as spies:
            runner.invoke(live, ["start", "alpha-session"])

        assert spies["runner"].run.call_count == 1


class TestTheRunningWindowIsAlwaysClosed:
    """AC #10 — a failure anywhere after ``→ running`` still leaves a startable
    session. Without this the row stays ``running`` and the session is
    unstartable for the full 90-second staleness threshold, which is the
    opposite of what AC #10 promises.
    """

    def test_a_raise_in_the_runners_constructor_still_marks_the_row_stopped(self, runner):
        with _start_harness(construct_error=RuntimeError("boom in __init__")) as spies:
            result = runner.invoke(live, ["start", "alpha-session"])

        spies["record"].mark_stopped.assert_called_once_with()
        assert result.exit_code == 1

    def test_a_failure_in_the_run_itself_does_not_double_mark(self, runner):
        """The runner's own ``finally`` already did it; a second call would
        raise ``InvalidSessionTransition`` from inside the error handler.
        """
        from src.core.live_check import BrokerUnreachableError

        with _start_harness(runner_error=BrokerUnreachableError("silent")) as spies:
            runner.invoke(live, ["start", "alpha-session"])

        spies["record"].mark_stopped.assert_not_called()

    def test_the_transition_happens_before_the_runner_is_constructed(self, runner):
        """Story 2.3's forward constraint: the ``get_sync_session`` block must
        close before the runner starts, or the row lock is held for hours and
        blocks every ``live status``.
        """
        order: list[str] = []
        with _start_harness() as spies:
            spies["service"].transition.side_effect = lambda *a, **k: (
                order.append("transition"),
                spies["row"],
            )[1]
            spies["runner_cls"].side_effect = lambda *a, **k: (
                order.append("construct"),
                spies["runner"],
            )[1]
            runner.invoke(live, ["start", "alpha-session"])

        assert order == ["transition", "construct"]


class TestStartRendersFirstPartyFailures:
    """Review fixes (2026-08-21): `start` must not funnel this codebase's own
    actionable text through the AR28 renderer's withholding fallback, and its
    except tuples must cover ``asyncio.CancelledError`` (a ``BaseException``
    since Python 3.8).
    """

    def test_a_database_failure_at_claim_prints_its_own_message(self, runner):
        """`create` already renders these (its `except (RuntimeError,
        DatabaseConnectionError, SQLAlchemyError)` arm); `start` must match.
        """
        from src.db.exceptions import DatabaseConnectionError

        error = DatabaseConnectionError("Cannot connect to PostgreSQL at localhost:5432")
        with _start_harness(resolve_error=error):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1
        assert "localhost:5432" in result.output

    def test_an_unconfigured_database_prints_the_remedy(self, runner):
        error = RuntimeError("Database not configured. Check DATABASE_URL in your environment")
        with _start_harness(resolve_error=error):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1
        assert "DATABASE_URL" in result.output

    def test_a_spec_that_no_longer_materialises_names_the_strategy(self, runner):
        """An unregistered ``strategy_id`` surfaces as a pydantic
        ``ValidationError`` from ``SessionSpec.from_stored`` — its message
        names the strategy and the registered list, and must reach the
        operator instead of a withheld bare type name.
        """
        from pydantic import ValidationError as PydanticValidationError

        error = PydanticValidationError.from_exception_data(
            "SessionSpec",
            [
                {
                    "type": "value_error",
                    "loc": ("strategies",),
                    "input": {},
                    "ctx": {
                        "error": ValueError(
                            "Unknown strategy 'ghost'. Registered strategies: "
                            "momentum, sma_crossover"
                        )
                    },
                }
            ],
        )
        with _start_harness() as spies:
            spies["spec_cls"].from_stored.side_effect = error
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1
        assert "ghost" in result.output
        spies["record"].mark_stopped.assert_called_once_with()

    def test_a_cancelled_error_is_rendered_through_the_exit_table(self, runner):
        """Uncaught, a ``CancelledError`` would bypass the AR28 rendering
        entirely and exit 1 only by interpreter default.
        """
        import asyncio

        with _start_harness(runner_error=asyncio.CancelledError("cancelled mid-run")):
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1
        assert "live start failed" in result.output

    def test_a_failing_record_construction_still_releases_the_row(self, runner):
        """The record's own constructor sits inside the guarded window now —
        a raise there must still put the row back to ``stopped`` (AC #10).
        """
        with _start_harness() as spies:
            spies["record_cls"].side_effect = [RuntimeError("boom"), spies["record"]]
            result = runner.invoke(live, ["start", "alpha-session"])

        assert result.exit_code == 1
        spies["record"].mark_stopped.assert_called_once_with()
