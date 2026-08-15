"""Unit tests for the pure connectivity-check vocabulary (Story 1.7).

``src/core/live_check.py`` owns the outcome vocabulary, AR28's exit-code table,
the Layer 1 pre-flight refusal and its log line, and the operator-facing report.
All of it is pure — no Nautilus, no socket, no event loop — which is what lets
the exit codes scripts depend on be tested here rather than against a broker.
"""

import ast
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core import live_check
from src.core.live_check import (
    EXIT_BROKER_UNREACHABLE,
    EXIT_CODES,
    EXIT_ERROR,
    EXIT_GATE_REFUSED,
    EXIT_OK,
    EXIT_USAGE,
    GATE_PHASE,
    LiveCheckOutcome,
    LiveCheckReport,
    classify_failure,
    preflight_gate,
    render_report,
)
from src.core.live_gate import GateFlags, GateMode, GateRefusalReason

pytestmark = pytest.mark.unit

# A distinctive non-paper account, used to prove masking: the raw value must
# never reach a log record or the rendered report, but its mask must.
REAL_LOOKING_ACCOUNT = "U7654321"
REAL_LOOKING_MASK = "***321"


def _settings(
    *,
    mode: str = "paper",
    port: int = 7497,
    account: str = "DU4076626",
    real_money_account: str = "",
) -> IBKRSettings:
    """Build settings with every gate-relevant field passed explicitly.

    Init kwargs outrank environment variables in pydantic-settings, so none of
    these fields can pick up a developer's shell. ``_env_file=None`` disables
    the dotenv *file* only — ``os.environ`` stays an active source — so any
    field the gate starts reading must be added here. Copied deliberately from
    ``tests/unit/core/test_live_gate.py``'s helper of the same name.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode=mode,
        ibkr_port=port,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
    )


def _events(captured: Sequence[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    return [record for record in captured if record.get("event") == name]


class TestExitCodeTable:
    """AR28's table is this story's contract with scripts. Assert it literally."""

    def test_exit_codes_match_ar28(self):
        assert EXIT_OK == 0
        assert EXIT_ERROR == 1
        assert EXIT_USAGE == 2
        assert EXIT_GATE_REFUSED == 3
        assert EXIT_BROKER_UNREACHABLE == 4

    def test_gate_refusal_is_distinct_from_every_other_outcome(self):
        """FR11's entire point: a script must tell refusal from failure."""
        assert EXIT_GATE_REFUSED != EXIT_BROKER_UNREACHABLE
        assert EXIT_GATE_REFUSED not in (EXIT_OK, EXIT_ERROR, EXIT_USAGE)
        assert EXIT_BROKER_UNREACHABLE not in (EXIT_OK, EXIT_ERROR, EXIT_USAGE)

    def test_every_outcome_has_an_exit_code(self):
        """An outcome added without a code must fail here, not KeyError at an operator."""
        missing = [outcome for outcome in LiveCheckOutcome if outcome not in EXIT_CODES]
        assert missing == [], f"outcomes with no exit code: {missing}"

    def test_only_ok_maps_to_zero(self):
        zeroes = [outcome for outcome, code in EXIT_CODES.items() if code == EXIT_OK]
        assert zeroes == [LiveCheckOutcome.OK]

    def test_interrupted_exits_one_not_one_hundred_thirty(self):
        """AR28's table has no 130; an interrupted check proved nothing, so it is a failure."""
        assert EXIT_CODES[LiveCheckOutcome.INTERRUPTED] == EXIT_ERROR


class TestReport:
    """The report is frozen and derives its exit code rather than storing one."""

    def test_exit_code_is_derived_from_outcome(self):
        for outcome in LiveCheckOutcome:
            report = LiveCheckReport(outcome=outcome, message="x")
            assert report.exit_code == EXIT_CODES[outcome]

    def test_report_is_frozen(self):
        report = LiveCheckReport(outcome=LiveCheckOutcome.OK, message="x")
        with pytest.raises(Exception):
            report.outcome = LiveCheckOutcome.ERROR  # type: ignore[misc]

    def test_instruments_missing_is_the_requested_minus_loaded_difference(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.OK,
            message="x",
            instruments_requested=("AAPL.NASDAQ", "MSFT.NASDAQ"),
            instruments_loaded=("AAPL.NASDAQ",),
        )
        assert report.instruments_missing == ("MSFT.NASDAQ",)

    def test_instruments_missing_is_empty_when_everything_loaded(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.OK,
            message="x",
            instruments_requested=("AAPL.NASDAQ",),
            instruments_loaded=("AAPL.NASDAQ", "SPY.ARCA"),
        )
        assert report.instruments_missing == ()

    def test_ok_reports_are_the_only_successful_ones(self):
        assert LiveCheckReport(outcome=LiveCheckOutcome.OK, message="x").ok is True
        for outcome in LiveCheckOutcome:
            if outcome is not LiveCheckOutcome.OK:
                assert LiveCheckReport(outcome=outcome, message="x").ok is False


class TestPreflightGatePermits:
    """A permitted configuration returns None and says so once, at info."""

    def test_paper_settings_return_none(self):
        assert preflight_gate(_settings(), GateFlags()) is None

    def test_permit_is_logged_with_the_phase_and_mode(self):
        with capture_logs() as captured:
            preflight_gate(_settings(), GateFlags())

        permits = _events(captured, "gate.static")
        assert len(permits) == 1
        assert permits[0]["phase"] == GATE_PHASE
        assert permits[0]["status"] == "ok"
        assert permits[0]["mode"] == GateMode.PAPER.value
        assert permits[0]["log_level"] == "info"

    def test_permit_emits_no_refusal(self):
        with capture_logs() as captured:
            preflight_gate(_settings(), GateFlags())

        assert _events(captured, "gate.refused") == []

    def test_none_cli_flags_means_no_declaration(self):
        """``None`` and ``GateFlags()`` must be the same thing, never 'unknown'."""
        assert preflight_gate(_settings(), None) is None


class TestPreflightGateRefuses:
    """Every Layer 1 refusal becomes exit code 3 with its reason preserved."""

    @pytest.mark.parametrize(
        ("kwargs", "expected_reason"),
        [
            ({"port": 4001}, GateRefusalReason.NON_PAPER_PORT),
            ({"mode": "live"}, GateRefusalReason.NON_PAPER_TRADING_MODE),
            (
                {"account": REAL_LOOKING_ACCOUNT},
                GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX,
            ),
            (
                {"real_money_account": REAL_LOOKING_ACCOUNT, "account": REAL_LOOKING_ACCOUNT},
                GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG,
            ),
        ],
    )
    def test_refusal_carries_reason_and_exit_code_three(self, kwargs, expected_reason):
        report = preflight_gate(_settings(**kwargs), GateFlags())

        assert report is not None
        assert report.outcome is LiveCheckOutcome.GATE_REFUSED
        assert report.exit_code == EXIT_GATE_REFUSED
        assert report.refusal_reason is expected_reason
        assert report.message

    def test_real_money_env_alone_is_refused(self):
        """The CLI exposes no --real-money, so this is the only crossing shape it can reach.

        It must refuse: a `NTRADER_REAL_MONEY_ACCOUNT` in the operator's
        environment can never be blessed by a command that cannot declare the
        matching flag.
        """
        report = preflight_gate(
            _settings(real_money_account=REAL_LOOKING_ACCOUNT, account=REAL_LOOKING_ACCOUNT),
            GateFlags(),
        )

        assert report is not None
        assert report.exit_code == EXIT_GATE_REFUSED

    def test_a_refusal_establishes_no_mode(self):
        report = preflight_gate(_settings(port=4001), GateFlags())

        assert report is not None
        assert report.mode is None


class TestPreflightGateLogsTheRefusal:
    """AC #4 — a Layer 1 refusal is logged nowhere in the codebase before this."""

    def test_refusal_logged_once_at_error_with_reason_and_phase(self):
        with capture_logs() as captured:
            preflight_gate(_settings(port=4001), GateFlags())

        refusals = _events(captured, "gate.refused")
        assert len(refusals) == 1
        assert refusals[0]["phase"] == GATE_PHASE
        assert refusals[0]["status"] == "failed"
        assert refusals[0]["reason"] == GateRefusalReason.NON_PAPER_PORT.value
        assert refusals[0]["log_level"] == "error"
        assert refusals[0]["message"]

    def test_refusal_level_matches_layer_twos_shipped_emission(self):
        """`live_account_gate` logs `gate.refused` at error; one event, one level."""
        from src.core import live_account_gate

        assert live_account_gate.STARTUP_PHASE != GATE_PHASE
        with capture_logs() as captured:
            preflight_gate(_settings(mode="live"), GateFlags())

        assert _events(captured, "gate.refused")[0]["log_level"] == "error"

    def test_no_permit_event_is_emitted_on_a_refusal(self):
        with capture_logs() as captured:
            preflight_gate(_settings(port=4001), GateFlags())

        assert _events(captured, "gate.static") == []

    def test_account_never_appears_unmasked_in_any_log_record(self):
        """NFR26 — masked to the last three characters, everywhere."""
        with capture_logs() as captured:
            preflight_gate(_settings(account=REAL_LOOKING_ACCOUNT), GateFlags())

        rendered = repr(captured)
        assert REAL_LOOKING_ACCOUNT not in rendered
        assert REAL_LOOKING_MASK in rendered


class TestClassifyFailure:
    """The pure exception→outcome map, so the driver holds no exit-code opinions."""

    @pytest.mark.parametrize(
        ("exception_name", "expected"),
        [
            ("GateRefusedError", LiveCheckOutcome.GATE_REFUSED),
            ("BrokerUnreachableError", LiveCheckOutcome.BROKER_UNREACHABLE),
            ("LiveNodeConfigError", LiveCheckOutcome.CONFIG_ERROR),
            ("LiveMarketDataError", LiveCheckOutcome.CONFIG_ERROR),
        ],
    )
    def test_named_exceptions_map_to_their_outcome(self, exception_name, expected):
        """Stand-ins by name: importing the real classes would drag in Nautilus.

        ``tests/component/core/test_live_check_driver.py`` asserts the real
        classes still carry these names, so the coupling cannot rot silently.
        """
        stand_in = type(exception_name, (Exception,), {})
        assert classify_failure(stand_in("boom")) is expected

    def test_subclasses_classify_as_their_base(self):
        base = type("GateRefusedError", (Exception,), {})
        derived = type("SomethingMoreSpecific", (base,), {})
        assert classify_failure(derived("boom")) is LiveCheckOutcome.GATE_REFUSED

    def test_keyboard_interrupt_is_interrupted(self):
        assert classify_failure(KeyboardInterrupt()) is LiveCheckOutcome.INTERRUPTED

    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionRefusedError("gateway not listening"),
            ConnectionResetError("gateway went away"),
            TimeoutError("socket timed out"),
        ],
    )
    def test_socket_level_failures_are_connectivity_failures(self, exc):
        """A refused socket is 'failed to connect', not a generic error."""
        assert classify_failure(exc) is LiveCheckOutcome.BROKER_UNREACHABLE

    def test_named_subclass_classifies_by_its_own_name(self):
        """MRO order is precedence: a named subclass matches before its base does."""
        specific = type("LiveNodeConfigError", (OSError,), {})
        assert classify_failure(specific("boom")) is LiveCheckOutcome.CONFIG_ERROR

    def test_anything_else_is_a_generic_error(self):
        assert classify_failure(RuntimeError("boom")) is LiveCheckOutcome.ERROR
        assert classify_failure(ValueError("boom")) is LiveCheckOutcome.ERROR

    def test_an_unrelated_class_named_like_ours_is_not_special_cased_by_accident(self):
        """Sanity: the map is keyed on the name, and that is a deliberate trade."""
        assert classify_failure(Exception("boom")) is LiveCheckOutcome.ERROR


class TestRenderReport:
    """The operator-facing summary — the only thing most runs will be read for."""

    def test_success_names_the_outcome_exit_code_and_counts(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.OK,
            message="connected and observed bars",
            mode=GateMode.PAPER,
            accounts="***626",
            bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            bars_received=2,
            counts_by_bar_type=(("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL", 2),),
            instruments_requested=("AAPL.NASDAQ",),
            instruments_loaded=("AAPL.NASDAQ",),
            elapsed_seconds=12.5,
        )

        rendered = render_report(report)

        assert "ok" in rendered
        assert "exit code 0" in rendered
        assert "***626" in rendered
        assert "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL" in rendered
        assert "2" in rendered

    def test_refusal_names_the_reason_and_the_message(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.GATE_REFUSED,
            message="IBKR_PORT 4001 is not a known paper port",
            refusal_reason=GateRefusalReason.NON_PAPER_PORT,
        )

        rendered = render_report(report)

        assert GateRefusalReason.NON_PAPER_PORT.value in rendered
        assert "IBKR_PORT 4001 is not a known paper port" in rendered
        assert "exit code 3" in rendered

    def test_missing_instruments_are_called_out(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.OK,
            message="connected",
            instruments_requested=("AAPL.NASDAQ", "NOPE.NASDAQ"),
            instruments_loaded=("AAPL.NASDAQ",),
        )

        rendered = render_report(report)

        assert "NOPE.NASDAQ" in rendered

    def test_shutdown_problems_are_surfaced(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.OK,
            message="connected",
            shutdown_problems=("dispose: RuntimeError: boom",),
        )

        assert "dispose: RuntimeError: boom" in render_report(report)

    def test_never_renders_an_unmasked_account(self):
        report = LiveCheckReport(
            outcome=LiveCheckOutcome.OK,
            message="connected",
            accounts="***321",
        )

        rendered = render_report(report)

        assert REAL_LOOKING_ACCOUNT not in rendered
        assert "***321" in rendered


class TestModulePurity:
    """The module stays framework-free — that is what keeps it unit-tier."""

    def test_module_imports_no_framework_or_io_library(self):
        source = Path(live_check.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = (
            "nautilus_trader",
            "ibapi",
            "sqlalchemy",
            "src.db",
            "src.services",
            "src.api",
            "src.core.live_node_builder",
            "src.core.live_check_driver",
        )
        offenders = [name for name in imported if name.startswith(forbidden)]

        assert offenders == [], f"live_check must stay framework-free, found: {offenders}"
