"""Epic-1 acceptance conformance: checking connectivity and the gate from the CLI.

One test per acceptance criterion in Story 1.7 of
``_bmad-output/planning-artifacts/prd-epic1-scope.md``. See
``test_epic1_ac_gate.py`` for why this suite sits alongside the tier suites
rather than replacing them.

Each criterion is asserted through both halves of the command: the driver, which
decides the outcome, and the Click surface, which turns that outcome into an exit
code an operator's script branches on. Neither half is asserted alone — the
exit-code contract is the product of the two, and a test of either one in
isolation would keep passing while the wiring between them rotted.

Nothing here contacts a broker (NFR32/NFR34): the driver's two broker-facing
seams — ``node_factory`` and ``account_verifier`` — are injected with doubles.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner
from structlog.testing import capture_logs

import src.cli.commands.live as live_module
import src.cli.main as cli_main
import src.utils.logging as logging_utils
from src.cli.commands.live import live
from src.cli.main import cli
from src.config import IBKRSettings
from src.core.live_check import (
    EXIT_BROKER_UNREACHABLE,
    EXIT_GATE_REFUSED,
    EXIT_OK,
    LiveCheckOutcome,
)
from src.core.live_check_driver import run_live_check
from src.core.live_gate import GateDecision, GateMode, GateRefusalReason
from tests.component.doubles import TestBarObserver, TestLiveNode
from tests.integration.core.epic1_criteria import criterion

pytestmark = pytest.mark.integration

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
PAPER_ACCOUNT = "DU4076626"

#: Where the CLI reaches for the driver, so a test can hand it a finished report.
_DRIVER = "src.cli.commands.live.run_live_check"


@pytest.fixture(autouse=True)
def _keep_the_shell_out_of_the_settings(monkeypatch):
    """``_env_file=None`` disables the dotenv file but not ``os.environ``."""
    for name in ("IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH", "IBKR_RATE_LIMIT"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


@pytest.fixture
def runner():
    return CliRunner()


def _settings(**overrides) -> IBKRSettings:
    fields = {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 4002,
        "ibkr_client_id": 1,
        "ibkr_live_client_id": 10,
        "ibkr_trading_mode": "paper",
        "tws_account": PAPER_ACCOUNT,
        "ntrader_real_money_account": "",
        "ibkr_market_data_lines": 100,
        "ibkr_rate_limit": 45,
    }
    fields.update(overrides)
    return IBKRSettings(_env_file=None, **fields)


async def _permits(node, settings, **kwargs) -> GateDecision:
    """A Layer 2 verifier that permits — it has its own acceptance tests."""
    return GateDecision(permitted=True, mode=GateMode.PAPER)


def _healthy_node(**overrides) -> TestLiveNode:
    fields = {
        "actors": [TestBarObserver([AAPL_1MIN], counts={AAPL_1MIN: 2})],
        "instrument_ids": ["AAPL.NASDAQ"],
        "connects_after": 1,
    }
    fields.update(overrides)
    return TestLiveNode(**fields)


@criterion("1.7a")
def test_a_complete_check_runs_the_whole_sequence_and_exits_zero(runner):
    """ "...it evaluates the gate, connects, verifies the account, subscribes to a
    configured instrument, prints the bars it receives, disconnects cleanly, and
    exits 0 (FR1-FR5)."
    """
    node = _healthy_node()

    report = run_live_check(
        _settings(),
        bar_types=[AAPL_1MIN],
        observe_seconds=0.0,
        connect_timeout=5.0,
        node_factory=lambda *a, **k: node,
        account_verifier=_permits,
    )

    assert report.outcome is LiveCheckOutcome.OK
    assert report.exit_code == EXIT_OK
    assert report.mode is GateMode.PAPER
    assert report.bar_types == (AAPL_1MIN,)
    assert report.bars_received == 2
    assert report.instruments_loaded == ("AAPL.NASDAQ",)
    assert node.built and node.stopped and node.disposed, "the node was not taken down cleanly"
    assert report.shutdown_problems == ()

    # ...and the command surface exits on that report and prints what it found.
    with patch(_DRIVER, return_value=report):
        result = runner.invoke(live, ["check", "--observe-seconds", "0"])

    assert result.exit_code == EXIT_OK
    assert "ok" in result.output
    assert "bars received: 2" in result.output


@criterion("1.7b")
def test_a_gate_refusal_prints_the_reason_and_exits_three(runner):
    """ "...it prints the specific refusal reason and exits with code 3, distinct
    from every other failure (FR11, AR28)."
    """
    report = run_live_check(
        _settings(ibkr_trading_mode="live"),
        bar_types=[AAPL_1MIN],
        observe_seconds=0.0,
        connect_timeout=5.0,
        node_factory=lambda *a, **k: pytest.fail("a node was built behind a refused gate"),
        account_verifier=_permits,
    )

    assert report.outcome is LiveCheckOutcome.GATE_REFUSED
    assert report.exit_code == EXIT_GATE_REFUSED == 3
    assert report.refusal_reason is GateRefusalReason.NON_PAPER_TRADING_MODE

    with patch(_DRIVER, return_value=report):
        result = runner.invoke(live, ["check"])

    assert result.exit_code == 3
    assert "non_paper_trading_mode" in result.output
    assert "IBKR_TRADING_MODE" in result.output, "the operator is not told what to change"
    # Scriptably distinct from every other failure, which is the whole of FR11.
    assert EXIT_GATE_REFUSED not in {EXIT_OK, EXIT_BROKER_UNREACHABLE}


@criterion("1.7c")
def test_nothing_is_built_or_connected_once_the_gate_has_refused():
    """ "...and no connection attempt appears in the logs."

    A property of the control flow, not an emergent hope: the driver returns
    before a loop, a node, a client config or a socket exists. The node factory
    is a landmine, and the log stream must carry the refusal and nothing that
    implies a connection was tried.
    """

    def _landmine(*args, **kwargs):
        raise AssertionError("the driver reached for a node behind a refused gate")

    with capture_logs() as records:
        report = run_live_check(
            _settings(tws_account="U1234567"),
            bar_types=[AAPL_1MIN],
            observe_seconds=0.0,
            connect_timeout=5.0,
            node_factory=_landmine,
            account_verifier=_permits,
        )

    assert report.outcome is LiveCheckOutcome.GATE_REFUSED
    assert report.refusal_reason is GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX

    events = {record.get("event") for record in records}
    assert "gate.refused" in events, "the refusal itself was never logged"
    assert events.isdisjoint(
        {"live_check.building", "live_check.connected", "live_check.observing"}
    ), f"a connection attempt was logged behind a refused gate: {sorted(events)}"

    # Nothing observable about the attempt was gathered, because nothing ran.
    assert report.accounts == ""
    assert report.mode is None
    assert report.bars_received == 0


@criterion("1.7d")
def test_a_gate_pass_with_an_unreachable_broker_exits_four(runner):
    """ "...it exits with code 4, so a script can tell 'refused to trade a live
    account' apart from 'failed to connect' (FR11, AR28)."
    """
    # `connects_after=-1`: the engines never report connected, which is what an
    # absent gateway looks like — the adapter logs and returns rather than raising.
    node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], connects_after=-1)

    report = run_live_check(
        _settings(),
        bar_types=[AAPL_1MIN],
        observe_seconds=0.0,
        connect_timeout=0.5,
        node_factory=lambda *a, **k: node,
        account_verifier=_permits,
    )

    assert report.outcome is LiveCheckOutcome.BROKER_UNREACHABLE
    assert report.exit_code == EXIT_BROKER_UNREACHABLE == 4
    assert node.disposed, "an unreachable broker still has to leave the node disposed"

    with patch(_DRIVER, return_value=report):
        result = runner.invoke(live, ["check"])

    assert result.exit_code == 4
    assert result.exit_code != EXIT_GATE_REFUSED, "4 and 3 must stay distinguishable"


@criterion("1.7e")
def test_the_command_streams_structured_structlog_output():
    """ "...it uses structured structlog console output consistent with existing
    CLI commands (FR48)."

    Consistency is asserted by identity, not by reading source: the `live` group
    inherits the root CLI's one `configure_logging`, and a second, divergent
    configuration is precisely the drift a text match would not see.
    """
    assert cli_main.configure_logging is logging_utils.configure_logging
    assert not hasattr(live_module, "configure_logging"), (
        "the live group configures logging itself instead of inheriting the root's"
    )

    with capture_logs() as records:
        run_live_check(
            _settings(),
            bar_types=[AAPL_1MIN],
            observe_seconds=0.0,
            connect_timeout=5.0,
            node_factory=lambda *a, **k: _healthy_node(),
            account_verifier=_permits,
        )

    by_event = {record["event"]: record for record in records if "event" in record}
    assert {"gate.static", "live_check.building", "live_check.observing"} <= set(by_event)

    # Structured means separate fields, not a formatted sentence.
    assert by_event["gate.static"]["phase"] == "gate:static"
    assert by_event["gate.static"]["status"] == "ok"
    building = by_event["live_check.building"]
    assert building["host"] == "127.0.0.1"
    assert building["port"] == 4002
    assert building["client_id"] == 10, "the historical client id reached the live logs"

    # And nothing in the stream carries a full account identifier (NFR26).
    assert PAPER_ACCOUNT not in repr(records)


@criterion("1.7f")
def test_the_live_group_is_wired_into_the_cli_and_lists_check(runner):
    """ "...check is listed, and the group is wired into src/cli/main.py alongside
    the existing command groups."
    """
    root = runner.invoke(cli, ["--help"])
    assert root.exit_code == 0
    assert "live" in root.output

    group = runner.invoke(live, ["--help"])
    assert group.exit_code == 0
    assert "check" in group.output

    assert cli.commands["live"] is live
    assert "check" in live.commands

    # No `--real-money` surface on a connectivity check, and none may be added:
    # crossing into real money is not something a check has any business
    # declaring, and with the flag unavailable a stale authorization in the
    # environment refuses at Layer 1 with exit 3, which is the right outcome.
    options = runner.invoke(live, ["check", "--help"]).output
    assert "--real-money" not in options
    assert {"--bar-type", "--observe-seconds", "--connect-timeout", "--require-bars"} <= {
        token for token in options.split() if token.startswith("--")
    }
