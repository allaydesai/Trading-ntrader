"""Unit tests for the `ntrader live check` CLI command (Story 1.7).

Every test patches ``run_live_check``, so nothing here constructs a
``TradingNode``, opens a socket or initialises the Nautilus C logging subsystem —
which is what keeps the file in the unit tier despite the transitive Nautilus
import. Precedent for that placement: ``test_validate_fmp.py`` imports
``nautilus_trader.model.data`` under the same marker.

What is under test is the exit-code contract (AR28/FR11) and the option surface,
not the driver — the driver has its own component suite.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.cli.commands.live import DEFAULT_BAR_TYPE, check, live
from src.cli.main import cli
from src.core.live_check import LiveCheckOutcome, LiveCheckReport
from src.core.live_gate import GateMode, GateRefusalReason

pytestmark = pytest.mark.unit

_DRIVER = "src.cli.commands.live.run_live_check"


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
