"""``OrderTriggered`` is unreachable through the IB adapter (Story 4.3, D-J).

The Story 3.3 code review routed "``OrderTriggered`` reaches the observer
unhandled, and reconciliation generates it" to Epic 4. Measured at Story 4.3
against the installed 1.220.0 wheel: there is no path by which an IB-connected
session produces one, so ``OrderEventObserver``'s closed dispatch set (and the
pinned ``EMITTED_ORDER_EVENTS``) stay unchanged, and ``live_order_path.py``'s
module docstring says why.

Each test below pins one leg of that unreachability. When an upgrade opens a
path, the matching test fails **by name** — the moment to add the dispatch
entry, with its own record and its own ``EMITTED_ORDER_EVENTS`` pin.
"""

import inspect

import pytest
from nautilus_trader.adapters.interactive_brokers import execution as ib_execution
from nautilus_trader.adapters.interactive_brokers.parsing.execution import MAP_ORDER_STATUS
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.model.enums import OrderStatus

pytestmark = pytest.mark.component


def test_no_ib_order_status_maps_to_triggered():
    assert OrderStatus.TRIGGERED not in set(MAP_ORDER_STATUS.values())


def test_the_ib_adapter_never_generates_a_triggered_event_itself():
    source = inspect.getsource(ib_execution)

    assert "generate_order_triggered" not in source
    assert "OrderStatus.TRIGGERED" not in source


def test_the_ib_report_parser_never_sets_a_trigger_time():
    """Reconciliation's other ``OrderTriggered`` branch needs a cancelled or
    expired report with ``ts_triggered > 0``."""
    source = inspect.getsource(
        ib_execution.InteractiveBrokersExecutionClient._parse_ib_order_to_order_status_report
    )

    assert "ts_triggered" not in source


def test_reconciliation_emits_it_only_on_those_two_shapes():
    """The premise the three tests above rest on: if reconciliation grows a
    third way to emit ``OrderTriggered``, they no longer prove unreachability."""
    source = inspect.getsource(LiveExecutionEngine._reconcile_order_report)

    assert source.count("_generate_order_triggered(") == 3
    assert "report.order_status == OrderStatus.TRIGGERED" in source
    assert source.count("report.ts_triggered > 0") == 2
