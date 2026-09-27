"""``live_node_probe.py --reconcile`` — Procedure P17a's tool (Story 4.2).

Covers only what Story 4.2 added: the flag is opt-in and reaches the run, a
refusal has its own RESULT reason, the RESULT suffix is appended only with the
flag, and the phase body runs — against the real ``LiveExecutionEngine``
harness from ``test_live_startup_reconcile_engine.py`` — only after the framework's own
pass has finished (the trader-started wait).
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.common.component import is_logging_initialized

from src.core.live_startup_reconcile import ReconciliationFailedError, ReconciliationFailure
from tests.component.core.test_live_startup_reconcile_engine import NVDA, _Harness

pytestmark = pytest.mark.component

PROBE_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "diagnostics" / "live_node_probe.py"
)


def _load_probe():
    spec = importlib.util.spec_from_file_location("live_node_probe_reconcile", PROBE_SCRIPT)
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
    def test_it_reaches_the_run(self, monkeypatch):
        seen = {}

        def run(run_seconds, **kwargs):
            seen.update(kwargs)
            return "loop_closed=True"

        assert _main(monkeypatch, ["--verify-account", "--reconcile"], run) == 0
        assert seen["reconcile"] is True

    def test_a_refusal_has_its_own_result_reason(self, monkeypatch, capsys):
        def run(run_seconds, **kwargs):
            raise ReconciliationFailedError(
                ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED, "a strategy is contradicted"
            )

        assert _main(monkeypatch, ["--verify-account", "--reconcile"], run) == 1
        out = capsys.readouterr().out.splitlines()
        [result] = [line for line in out if "RESULT" in line]
        assert result.startswith(
            "RESULT: fail reason=reconcile_refused failure=strategy_position_contradicted "
        )

    def test_it_refuses_to_run_without_the_account_gate(self, monkeypatch):
        """AR39: ``reconcile`` runs after ``gate:account``, never instead of it."""

        def run(run_seconds, **kwargs):
            raise AssertionError("the probe ran reconcile without --verify-account")

        with pytest.raises(SystemExit) as exited:
            _main(monkeypatch, ["--reconcile"], run)
        assert exited.value.code == 2


class _FakeNode:
    cache = SimpleNamespace(positions_open=lambda: [])

    def build(self) -> None:
        pass

    async def run_async(self) -> None:
        await asyncio.Event().wait()


def _real_run(monkeypatch, *, reconcile: bool) -> tuple[str, list]:
    settings = SimpleNamespace(
        ibkr_host="127.0.0.1", ibkr_port=4002, ibkr_live_client_id=10, ibkr_connection_timeout=1
    )
    calls: list = []

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

    def reconciled(node, run_task, settings_arg, loop, local_before):
        calls.append(local_before)
        return " reconcile=ok positions=1 discrepancies=1"

    monkeypatch.setattr(probe, "IBKRSettings", lambda: settings)
    monkeypatch.setattr(probe, "build_trading_node", lambda *args, **kwargs: _FakeNode())
    monkeypatch.setattr(probe, "_await_connected", connected)
    monkeypatch.setattr(probe, "_reconcile", reconciled)
    monkeypatch.setattr(probe, "_shutdown", shutdown)
    try:
        return probe._run(0, verify_account=False, reconcile=reconcile), calls
    finally:
        asyncio.set_event_loop(None)


class TestTheResultLine:
    def test_the_reconcile_is_appended_to_the_result_suffix(self, monkeypatch):
        detail, calls = _real_run(monkeypatch, reconcile=True)

        assert detail == "loop_closed=True reconcile=ok positions=1 discrepancies=1"
        assert calls == [()], "the snapshot was not taken, or not handed to the phase"

    def test_without_the_flag_nothing_runs_and_the_suffix_is_unchanged(self, monkeypatch):
        detail, calls = _real_run(monkeypatch, reconcile=False)

        assert detail == "loop_closed=True" and calls == []


class TestThePhaseBody:
    def test_it_waits_for_the_trader_then_reconciles_against_the_real_engine(
        self, monkeypatch, capsys
    ):
        """A fresh in-memory cache against a broker holding +10: the
        framework's pass imports it, and the phase body names that as the
        framework's resolution — exactly what P17a expects to see live."""
        h = _Harness({NVDA.id: (10, 100.0)})
        order: list[str] = []
        before = ()
        try:
            assert h.native() is True

            async def trader_started(node, run_task, deadline, timeout, settings, log):
                order.append("trader_started")

            async def read(node, *, log):
                order.append("read")
                from tests.component.core.test_live_startup_reconcile_engine import _state

                return _state((NVDA.id, "10", "100"))

            monkeypatch.setattr(probe, "await_trader_started", trader_started)
            real = probe.reconcile_at_startup

            async def reconcile_at_startup(node, *, log, local_before):
                return await real(node, log=log, local_before=local_before, read_state=read)

            monkeypatch.setattr(probe, "reconcile_at_startup", reconcile_at_startup)
            suffix = probe._reconcile(h.node, None, None, h.loop, before)
        finally:
            h.close()

        assert order == ["trader_started", "read"]
        assert suffix == " reconcile=ok positions=1 discrepancies=1"
        assert "[probe] reconciled NVDA.NASDAQ qty=+10" in capsys.readouterr().out
