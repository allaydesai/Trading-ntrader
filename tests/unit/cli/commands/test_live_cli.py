"""Unit tests for the `ntrader live check` CLI command (Story 1.7).

Every test patches ``run_live_check``, so nothing here constructs a
``TradingNode``, opens a socket or initialises the Nautilus C logging subsystem —
which is what keeps the file in the unit tier despite the transitive Nautilus
import. Precedent for that placement: ``test_validate_fmp.py`` imports
``nautilus_trader.model.data`` under the same marker.

What is under test is the exit-code contract (AR28/FR11) and the option surface,
not the driver — the driver has its own component suite.
"""

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
