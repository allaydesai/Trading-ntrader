"""The IB adapter's position-update reports are switched off (Story 4.3, D-B).

PO ruling 1A (2026-09-27). The adapter's own runtime position path —
``positionUpdate`` → ``_on_position_update`` → ``create_task(self._handle_position_update(p))``
→ ``_send_position_status_report`` — is what produced the P11/P12 double-count
phantom on every strategy entry, and it is blind to a position going flat. Story
4.3's verified cycle (``live_runtime_reconcile``) owns runtime position alignment
instead, so this patch makes the adapter's handler send nothing.

Three groups. The *canaries* drive the adapter's **real** functions on a
stand-in and pin the defect the patch exists for — each fails by name on an
upgrade that fixes or moves it, which is the moment to reconsider the patch.
The *proofs* drive the same real dispatch after the patch is installed. The
*wiring* pins that the node builder's factory installs it.
"""

import asyncio
import inspect
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.adapters.interactive_brokers.execution import (
    InteractiveBrokersExecutionClient as IBExecClient,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveExecClientFactory as StockFactory,
)
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from structlog.testing import capture_logs

from src.core import live_node_builder
from src.core.live_exec_position_reports import (
    DEFERRED_EVENT,
    HANDLER_ATTRIBUTE,
    TRACKING_ATTRIBUTE,
    install_position_report_suppression,
)

pytestmark = pytest.mark.component

NVDA = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
CON_ID = 4815747
RAW_ACCOUNT = "DU4076626"


class _Silent:
    def info(self, *args, **kwargs):
        pass

    warning = error = debug = info


def _stand_in() -> SimpleNamespace:
    """Just enough of an ``InteractiveBrokersExecutionClient`` for the adapter's
    own ``_on_position_update`` / ``_handle_position_update`` to run unmodified.
    ``sent`` records every report the adapter hands the engine; ``tasks`` holds
    the coroutine ``_on_position_update`` schedules.
    """

    async def _get_instrument(contract):
        return NVDA

    client = SimpleNamespace(
        _known_positions={},
        _log=_Silent(),
        instrument_provider=SimpleNamespace(get_instrument=_get_instrument),
        _cache=SimpleNamespace(instrument=lambda instrument_id: NVDA),
        _handle_data=lambda data: None,
        account_id=AccountId(f"INTERACTIVE_BROKERS-{RAW_ACCOUNT}"),
        _clock=SimpleNamespace(timestamp_ns=lambda: 1),
        _order_avg_prices={},
        sent=[],
        tasks=[],
    )
    client._send_position_status_report = client.sent.append
    client.create_task = client.tasks.append
    # The class's own method, bound to the stand-in — what `self.` resolves to
    # on a real client until an instance attribute shadows it.
    client._handle_position_update = lambda p: IBExecClient._handle_position_update(client, p)
    return client


def _update(quantity: int) -> SimpleNamespace:
    return SimpleNamespace(
        account_id=RAW_ACCOUNT,
        contract=SimpleNamespace(conId=CON_ID, secType="STK"),
        quantity=Decimal(quantity),
        avg_cost=101.25,
    )


def _deliver(client: SimpleNamespace, quantity: int) -> None:
    """The adapter's real dispatch: ``_on_position_update`` schedules the
    handler through ``self.create_task``; run what it scheduled."""
    IBExecClient._on_position_update(client, _update(quantity))
    asyncio.run(client.tasks.pop())


class TestTheAdapterDefect:
    """Canaries: what the stock handler does, driven for real (Task 1.3)."""

    def test_an_untracked_update_then_the_own_fill_sends_two_reports(self):
        """F5, the P11/P12 phantom: flat means untracked; ``0 → 22`` before the
        strategy's own ``execDetails`` is reported as an external change, the
        ``execDetails`` then makes the tracked quantity 44, and the next ``22``
        is reported again. Two reports for one 22-share order."""
        client = _stand_in()

        _deliver(client, 22)
        IBExecClient._update_position_tracking_from_execution(
            client,
            SimpleNamespace(conId=CON_ID),
            SimpleNamespace(side="BOT", shares=Decimal(22)),
        )
        _deliver(client, 22)

        assert [(str(r.instrument_id), str(r.quantity)) for r in client.sent] == [
            ("NVDA.NASDAQ", "22"),
            ("NVDA.NASDAQ", "22"),
        ]
        assert client._known_positions == {CON_ID: Decimal(22)}

    def test_a_position_going_flat_sends_nothing(self):
        """F4: the stream is blind to flat — so a verified cycle is needed anyway."""
        client = _stand_in()
        client._known_positions[CON_ID] = Decimal(22)

        _deliver(client, 0)

        assert client.sent == []

    def test_the_dispatch_still_goes_through_the_instance_attribute(self):
        """What makes an instance-level patch effective: the handler is looked
        up on ``self`` at call time, and the stock handler still reports."""
        on_update = inspect.getsource(IBExecClient._on_position_update)
        handler = inspect.getsource(IBExecClient._handle_position_update)
        connect = inspect.getsource(IBExecClient._connect)

        assert "self._handle_position_update(" in on_update
        assert "self._send_position_status_report(" in handler
        assert 'f"positionUpdate-{account}", self._on_position_update' in connect


class TestTheSuppression:
    """Proofs: the same real dispatch, after the patch."""

    def test_the_real_dispatch_sends_no_report_and_says_so(self):
        client = _stand_in()
        install_position_report_suppression(client)

        with capture_logs() as logs:
            _deliver(client, 22)
            _deliver(client, 0)

        assert client.sent == [], "the adapter's position report reached the engine"
        records = [e for e in logs if e["event"] == DEFERRED_EVENT]
        assert [(r["con_id"], r["reported_quantity"]) for r in records] == [
            (CON_ID, "22"),
            (CON_ID, "0"),
        ]
        assert all(r["log_level"] == "info" for r in records)
        assert all("known_quantity" in r for r in records), "D-B names the adapter's own view"

    def test_no_record_carries_the_account(self):
        """NFR26: the update carries the raw account; nothing logged may."""
        client = _stand_in()
        install_position_report_suppression(client)

        with capture_logs() as logs:
            _deliver(client, 22)

        assert logs, "premise: the record was captured"
        assert all(RAW_ACCOUNT not in repr(record) for record in logs)

    def test_a_malformed_update_never_raises(self):
        """It runs inside an adapter task; a raise would only be the adapter's
        noise, but a diagnostic must never be the thing that fails."""

        class _Hostile:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        client = _stand_in()
        install_position_report_suppression(client)

        asyncio.run(getattr(client, HANDLER_ATTRIBUTE)(_Hostile()))

        assert client.sent == []

    @pytest.mark.parametrize("missing", [HANDLER_ATTRIBUTE, TRACKING_ATTRIBUTE])
    def test_a_client_whose_shape_moved_is_refused_at_build_time(self, missing):
        """A silent skip would bring the phantom back unnoticed — the avg-px
        precedent: loud at build time."""
        client = _stand_in()
        delattr(client, missing)

        with pytest.raises(AttributeError, match=missing):
            install_position_report_suppression(client)

    def test_installing_twice_is_harmless(self):
        client = _stand_in()
        install_position_report_suppression(client)
        install_position_report_suppression(client)

        _deliver(client, 22)

        assert client.sent == []


class TestTheFactoryInstallsIt:
    def test_the_factory_wrapper_installs_the_suppression(self, monkeypatch):
        """Drives our ``create`` for real with only the upstream call stubbed,
        so it fails if the wrapper stops installing the patch."""
        built = _stand_in()
        monkeypatch.setattr(StockFactory, "create", staticmethod(lambda **kwargs: built))

        returned = live_node_builder.InteractiveBrokersLiveExecClientFactory.create(
            loop=None, name="IB", config=None, msgbus=None, cache=None, clock=None
        )
        _deliver(returned, 22)

        assert returned is built
        assert built.sent == []

    def test_the_builder_module_calls_the_install(self):
        source = Path(inspect.getfile(live_node_builder)).read_text(encoding="utf-8")

        assert "install_position_report_suppression(client)" in source
