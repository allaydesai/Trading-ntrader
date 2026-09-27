"""``live_node_probe.py --read-broker-state`` — Procedure P15's tool (Story 4.1).

The probe predates any test of its own; these cover only what Story 4.1 added:
the flag is opt-in (P1/P2's documented output is unchanged without it), the read
prints the broker's view masked and counts it into the RESULT line, and a failed
read has its own RESULT reason — so it can never be read as a flat account.
The read runs against the real adapter stack Story 4.1's component suite builds.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.common.component import is_logging_initialized

from src.core.live_broker_state import BrokerStateFailure, BrokerStateUnavailableError
from tests.component.core.test_live_broker_state_adapter import (
    ACCOUNT,
    NVDA_CON_ID,
    _positions,
    _push_account_summary,
    _stack,
)

pytestmark = pytest.mark.component

PROBE_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "diagnostics" / "live_node_probe.py"
)


def _load_probe():
    """Import the script by path — ``scripts/`` is not an importable package."""
    spec = importlib.util.spec_from_file_location("live_node_probe", PROBE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before


def _main(monkeypatch, argv, run):
    monkeypatch.setattr(probe, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_run", run)
    monkeypatch.setattr(sys, "argv", ["live_node_probe.py", *argv])
    return probe.main()


class TestTheFlag:
    def test_it_is_off_by_default(self, monkeypatch):
        seen = {}

        def run(run_seconds, **kwargs):
            seen.update(kwargs)
            return "loop_closed=True"

        assert _main(monkeypatch, ["--run-seconds", "1"], run) == 0
        # Story 4.2 widened the flag set deliberately (`--reconcile`, P17a).
        assert seen == {"verify_account": False, "read_state": False, "reconcile": False}

    def test_it_reaches_the_run(self, monkeypatch):
        seen = {}

        def run(run_seconds, **kwargs):
            seen.update(kwargs)
            return "loop_closed=True"

        assert _main(monkeypatch, ["--read-broker-state", "--verify-account"], run) == 0
        assert seen == {"verify_account": True, "read_state": True, "reconcile": False}

    def test_a_failed_read_has_its_own_result_reason(self, monkeypatch, capsys):
        def run(run_seconds, **kwargs):
            raise BrokerStateUnavailableError(BrokerStateFailure.TIMEOUT, "no answer")

        assert _main(monkeypatch, ["--read-broker-state"], run) == 1
        out = capsys.readouterr().out.splitlines()
        [result] = [line for line in out if "RESULT" in line]
        assert result.startswith("RESULT: fail reason=broker_state_unavailable failure=timeout ")
        assert "[probe] broker state failed: no answer" in out


class _FakeNode:
    """Just the lifecycle ``_run`` drives; the connect/read/shutdown steps are
    replaced, so only ``_run``'s own composition of the RESULT line is real."""

    def build(self) -> None:
        pass

    async def run_async(self) -> None:
        await asyncio.Event().wait()


def _real_run(monkeypatch, *, read_state: bool) -> str:
    settings = SimpleNamespace(
        ibkr_host="127.0.0.1", ibkr_port=4002, ibkr_live_client_id=10, ibkr_connection_timeout=1
    )

    async def connected(node, run_task, timeout):
        return None

    def shutdown(node, run_task, loop):
        run_task.cancel()
        try:
            loop.run_until_complete(run_task)
        except asyncio.CancelledError:
            pass
        loop.close()
        return []

    monkeypatch.setattr(probe, "IBKRSettings", lambda: settings)
    monkeypatch.setattr(probe, "build_trading_node", lambda *args, **kwargs: _FakeNode())
    monkeypatch.setattr(probe, "_await_connected", connected)
    monkeypatch.setattr(probe, "_read_broker_state", lambda node, loop: 2)
    monkeypatch.setattr(probe, "_shutdown", shutdown)
    try:
        return probe._run(0, verify_account=False, read_state=read_state)
    finally:
        asyncio.set_event_loop(None)


class TestTheResultLine:
    def test_the_read_is_counted_into_the_result_suffix(self, monkeypatch):
        assert _real_run(monkeypatch, read_state=True) == (
            "loop_closed=True broker_state=ok positions=2"
        )

    def test_without_the_flag_the_suffix_is_unchanged(self, monkeypatch):
        """P1's documented RESULT line, byte for byte."""
        assert _real_run(monkeypatch, read_state=False) == "loop_closed=True"


class TestTheRead:
    def test_it_prints_the_broker_view_masked_and_counts_positions(self, capsys):
        loop = asyncio.new_event_loop()
        try:

            async def build():
                stack = _stack()
                _push_account_summary(stack.exec_client)
                stack.socket.script = _positions((ACCOUNT, "NVDA", NVDA_CON_ID, 22, 180.5))
                return stack

            stack = loop.run_until_complete(build())
            count = probe._read_broker_state(stack.node, loop)
        finally:
            loop.close()

        out = capsys.readouterr().out
        assert count == 1
        assert "[probe] broker state account=***626 positions=1 flat=False" in out
        assert "[probe] position NVDA.NASDAQ qty=+22 avg_price=180.5" in out
        assert "[probe] cash USD total_cash=100000.52" in out
        assert ACCOUNT not in out
