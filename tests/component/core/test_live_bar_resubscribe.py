"""The IB adapter's bar resubscribe is made to work (Story 5.9, AC #1, #2, #4).

Nautilus 1.220's ``_subscribe`` stores a historical-bars subscription's handle
as ``partial(subscribe_historical_bars, bar_type=, contract=, use_rth=,
handle_revised_bars=, start=)`` — a ``start`` the method does not take and no
``params``, which it requires — so ``_resubscribe_all`` raises ``TypeError``
on every reconnect, logs it, and the session never sees another bar (live,
P23 2026-10-12).

Three groups, the ``test_live_exec_position_reports`` shape. The *canaries*
drive the stock adapter on a real ``InteractiveBrokersClient`` (no socket: its
``_eclient`` is a spy) and pin the defect; each fails by name on an upgrade
that fixes or moves it, which is when the shim becomes deletable. The *proofs*
drive the same real client with the shim installed. The *wiring* pins that the
node builder's exec factory installs it on the shared client.
"""

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.common import IBContract
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveExecClientFactory as StockFactory,
)
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import TraderId
from structlog.testing import capture_logs

from src.core import live_node_builder
from src.core.live_bar_resubscribe import (
    _STOCK_ATTRIBUTE,
    _STOCK_ERROR_ATTRIBUTE,
    EMITTED_BAR_FEED_EVENTS,
    ERROR_HANDLER_ATTRIBUTE,
    FAILURES_ATTRIBUTE,
    METHOD_ATTRIBUTE,
    RESUBSCRIBE_FAILED_EVENT,
    RESUBSCRIBED_EVENT,
    BarFeedLostError,
    BarFeedWatch,
    install_bar_resubscribe_fix,
)
from src.core.live_catch_up_gate import CATCH_UP_EVENT, CATCH_UP_ORDER_WITHHELD_EVENT

pytestmark = pytest.mark.component

SOURCE = "_bmad-output/planning-artifacts/epics.md"
AC_5_9A = (
    "the reconnect path's bar resubscribe restores the subscription, one bars.resubscribed "
    "record names the bar type, and live bars resume"
)
AC_5_9B = (
    "a failed bar resubscribe is recorded once as bars.resubscribe_failed with the bar type and "
    "error type, and the session does not report a healthy, trading state while deaf"
)
AC_5_9D = (
    "a canary pinned against the adapter's own source goes red if the stored handle's shape or "
    "subscribe_historical_bars's signature changes"
)

BAR_TYPE = BarType.from_str("NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL")
CONTRACT = IBContract(
    secType="STK", symbol="NVDA", exchange="SMART", primaryExchange="NASDAQ", currency="USD"
)
RAW_ACCOUNT = "DU4076626"
ADAPTER_SOURCE = Path(inspect.getfile(InteractiveBrokersClient.subscribe_historical_bars))
DELETABLE = "src/core/live_bar_resubscribe.py and its call site may be deletable"


class _Client:
    """A real ``InteractiveBrokersClient`` on its own loop, its socket a spy."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        clock = LiveClock()
        self.client = InteractiveBrokersClient(
            loop=self.loop,
            msgbus=MessageBus(trader_id=TraderId("PAPER-a1b2c3d4"), clock=clock),
            cache=Cache(),
            clock=clock,
            host="127.0.0.1",
            port=4002,
            client_id=10,
        )
        self.requests: list[dict] = []
        self.client._eclient = SimpleNamespace(
            reqHistoricalData=lambda **kwargs: self.requests.append(kwargs),
            cancelHistoricalData=lambda *args, **kwargs: None,
        )

    def run(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    def subscribe(self) -> None:
        """The adapter's initial call, as ``data.py:263-269`` makes it."""
        self.run(
            self.client.subscribe_historical_bars(
                bar_type=BAR_TYPE,
                contract=CONTRACT,
                use_rth=True,
                handle_revised_bars=False,
                params={"start_ns": None},
            )
        )

    def resubscribe_all(self) -> None:
        self.run(self.client._resubscribe_all())

    def ib_error(self, code: int, text: str = "refused") -> None:
        """IB's error callback for the bar subscription's request, as the
        adapter's reader dispatches it (``client/error.py``)."""
        req_id = self.client._subscriptions.get(name=str(BAR_TYPE)).req_id
        self.run(self.client.process_error(req_id=req_id, error_code=code, error_string=text))

    def close(self) -> None:
        self.loop.close()


@pytest.fixture
def ib():
    harness = _Client()
    yield harness
    harness.close()


def _events(logs, name):
    return [entry for entry in logs if entry["event"] == name]


def _no_account(logs) -> None:
    for entry in logs:
        assert RAW_ACCOUNT not in repr(entry), entry


class TestTheAdapterDefect:
    """Canaries: the stock adapter, driven and read for real."""

    @pytest.mark.acceptance_criterion("5.9d", AC_5_9D, SOURCE)
    def test_the_stock_resubscribe_requests_nothing(self, ib):
        """F1 without a broker: the reconnect re-issues no history request."""
        ib.subscribe()
        assert len(ib.requests) == 1

        ib.resubscribe_all()

        assert len(ib.requests) == 1, f"the stock resubscribe works now: {DELETABLE}"

    @pytest.mark.acceptance_criterion("5.9d", AC_5_9D, SOURCE)
    def test_the_stored_handle_passes_start_and_no_params(self, ib):
        """F2/F3: the shape both the defect and the shim depend on."""
        ib.subscribe()
        handle = ib.client._subscriptions.get(name=str(BAR_TYPE)).handle

        assert sorted(handle.keywords) == [
            "bar_type",
            "contract",
            "handle_revised_bars",
            "start",
            "use_rth",
        ], f"the stored handle's keywords changed: {DELETABLE}"

    @pytest.mark.acceptance_criterion("5.9d", AC_5_9D, SOURCE)
    def test_the_method_takes_params_and_no_start(self):
        parameters = inspect.signature(
            InteractiveBrokersClient.subscribe_historical_bars
        ).parameters

        assert "params" in parameters and "start" not in parameters, (
            f"subscribe_historical_bars's signature changed: {DELETABLE}"
        )

    def test_subscribe_compares_the_method_it_was_handed_by_identity(self):
        """F4: an instance attribute wrapper is what ``_subscribe`` stores only
        because it looks ``self.subscribe_historical_bars`` up at call time."""
        source = ADAPTER_SOURCE.read_text()

        assert "if subscription_method == self.subscribe_historical_bars:" in source
        assert "start=start," in inspect.getsource(
            InteractiveBrokersClient.subscribe_historical_bars
        )

    def test_resubscribe_all_swallows_a_failed_handle(self):
        """F6: why a failed resubscribe is invisible without this story."""
        source = inspect.getsource(InteractiveBrokersClient._resubscribe_all)

        assert "except Exception as e:" in source
        assert 'self._log.exception(f"Failed to resubscribe to {subscription}", e)' in source

    def test_a_replayed_bar_is_stamped_with_its_period_end(self, ib):
        """F8: a catch-up bar's ``ts_init`` is not its arrival, so only the wall
        clock at handling can recognise one (the gate's whole premise)."""
        bar = SimpleNamespace(date="1791830880")

        ts_init = ib.run(ib.client._ib_bar_to_ts_init(bar, BAR_TYPE))

        assert ts_init == (1_791_830_880 + 60) * 1_000_000_000
        source = inspect.getsource(InteractiveBrokersClient._process_bar_data)
        assert "ts_init = await self._ib_bar_to_ts_init(bar, bar_type)" in source

    def test_a_bar_is_stamped_ts_event_at_its_open(self, ib):
        """Code review P5: the gate computes a bar's close as ``ts_event +
        interval``, which holds only while IB's ``ts_event`` is the bar's open
        (measured live, P24 2026-10-13). An upgrade that stamps the close would
        give every bar one extra interval of grace, silently."""
        bar = SimpleNamespace(date="1791830880")

        ts_event = ib.run(ib.client._ib_bar_to_ts_event(bar, BAR_TYPE))

        assert ts_event == 1_791_830_880 * 1_000_000_000

    def test_a_subscription_error_reaches_the_handler_by_instance_lookup(self):
        """P9's premise: ``process_error`` looks ``_handle_subscription_error``
        up on the instance, and the adapter retries only these codes itself,
        logging any other as unknown and leaving the subscription dead."""
        dispatch = inspect.getsource(InteractiveBrokersClient.process_error)
        handler = inspect.getsource(InteractiveBrokersClient._handle_subscription_error)

        assert "await self._handle_subscription_error(req_id, error_code, error_string)" in dispatch
        assert "if error_code in [10189, 366, 102]:" in handler
        assert "elif error_code == 10182:" in handler
        assert 'f"Unknown subscription error: {error_code} for req_id {req_id}"' in handler


class TestTheShim:
    @pytest.mark.acceptance_criterion("5.9a", AC_5_9A, SOURCE)
    def test_the_resubscribe_reissues_the_history_request(self, ib):
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()

        with capture_logs() as logs:
            ib.resubscribe_all()

        first, again = ib.requests
        assert again["reqId"] == first["reqId"]
        assert again["keepUpToDate"] is True
        assert again["durationStr"] == first["durationStr"] == "18000 S"
        [record] = _events(logs, RESUBSCRIBED_EVENT)
        assert record["bar_type"] == str(BAR_TYPE)
        assert record["log_level"] == "info"
        _no_account(logs)

    def test_the_stored_handle_keeps_the_shape_the_adapter_reads(self, ib):
        """F3: ``process_historical_data`` reads the handle's ``start`` and
        ``process_historical_data_update`` requires a ``partial`` with
        ``handle_revised_bars``."""
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()
        handle = ib.client._subscriptions.get(name=str(BAR_TYPE)).handle

        assert handle.func is getattr(ib.client, METHOD_ATTRIBUTE)
        assert handle.keywords["start"] is not None
        assert handle.keywords["handle_revised_bars"] is False

    def test_the_initial_call_is_unchanged_and_silent(self, ib):
        install_bar_resubscribe_fix(ib.client)

        with capture_logs() as logs:
            ib.subscribe()

        assert len(ib.requests) == 1
        assert _events(logs, RESUBSCRIBED_EVENT) == []

    @pytest.mark.acceptance_criterion("5.9b", AC_5_9B, SOURCE)
    def test_a_failed_resubscribe_is_named_noted_and_re_raised(self, ib):
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()

        def _refuse(**kwargs):
            raise ConnectionResetError(f"socket closed for {RAW_ACCOUNT}")

        ib.client._eclient.reqHistoricalData = _refuse

        with capture_logs() as logs:
            ib.resubscribe_all()

        [record] = _events(logs, RESUBSCRIBE_FAILED_EVENT)
        assert record["bar_type"] == str(BAR_TYPE)
        assert record["error_type"] == "ConnectionResetError"
        assert record["log_level"] == "error"
        assert getattr(ib.client, FAILURES_ATTRIBUTE) == [(str(BAR_TYPE), "ConnectionResetError")]
        assert _events(logs, RESUBSCRIBED_EVENT) == []
        _no_account(logs)

    def test_a_failed_resubscribe_reaches_the_adapter_unchanged(self, ib):
        """D-B: re-raised, so the adapter's own ``Failed to resubscribe`` line
        is still written. ``_resubscribe_all`` swallows it either way, so this
        calls the stored handle directly: it is the only place a swallow by the
        shim would show."""
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()
        handle = ib.client._subscriptions.get(name=str(BAR_TYPE)).handle

        def _refuse(**kwargs):
            raise ConnectionResetError("socket closed")

        ib.client._eclient.reqHistoricalData = _refuse

        with pytest.raises(ConnectionResetError):
            ib.run(handle())

    def test_a_clean_resubscribe_notes_no_failure(self, ib):
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()

        ib.resubscribe_all()

        assert getattr(ib.client, FAILURES_ATTRIBUTE) == []

    def test_installing_twice_wraps_once(self, ib):
        install_bar_resubscribe_fix(ib.client)
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()

        with capture_logs() as logs:
            ib.resubscribe_all()

        assert len(ib.requests) == 2
        assert len(_events(logs, RESUBSCRIBED_EVENT)) == 1
        stock = getattr(ib.client, _STOCK_ATTRIBUTE)
        assert stock.__func__ is InteractiveBrokersClient.subscribe_historical_bars
        stock_handler = getattr(ib.client, _STOCK_ERROR_ATTRIBUTE)
        assert stock_handler.__func__ is InteractiveBrokersClient._handle_subscription_error

    def test_a_client_without_the_method_is_loud_at_build_time(self):
        with pytest.raises(AttributeError):
            install_bar_resubscribe_fix(SimpleNamespace())


class TestAnIbRefusalOfTheResubscribe:
    """Code review P9 (DN1, Allay 2026-10-13). IB refuses a history request
    through its error callback, not by a raise, so ``bars.resubscribed`` (the
    request was re-issued) can be followed by a refusal the adapter only logs.
    An error on a re-issued request that the adapter does not answer itself is
    a lost bar feed (R2)."""

    def _resubscribed(self, ib) -> None:
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()
        ib.resubscribe_all()

    @pytest.mark.acceptance_criterion("5.9b", AC_5_9B, SOURCE)
    def test_a_refusal_after_the_resubscribe_is_a_lost_feed(self, ib):
        self._resubscribed(ib)

        with capture_logs() as logs:
            ib.ib_error(162, f"Historical Market Data Service error message: {RAW_ACCOUNT}")

        [record] = _events(logs, RESUBSCRIBE_FAILED_EVENT)
        assert record["bar_type"] == str(BAR_TYPE)
        assert record["error_type"] == "ib_error"
        assert record["error_code"] == 162
        assert record["log_level"] == "error"
        assert getattr(ib.client, FAILURES_ATTRIBUTE) == [(str(BAR_TYPE), "IB error 162")]
        _no_account(logs)

    def test_the_watch_stops_on_it(self, ib):
        self._resubscribed(ib)
        ib.ib_error(162)
        inner = _SpyReconciler()
        watch = BarFeedWatch(inner, _node_with_ib_client(ib.client))

        with pytest.raises(BarFeedLostError) as caught:
            asyncio.run(watch.on_tick())

        assert inner.ticks == 0
        assert "IB error 162" in str(caught.value)

    def test_an_error_before_any_reconnect_is_the_adapters_alone(self, ib):
        install_bar_resubscribe_fix(ib.client)
        ib.subscribe()

        with capture_logs() as logs:
            ib.ib_error(162)

        assert getattr(ib.client, FAILURES_ATTRIBUTE) == []
        assert _events(logs, RESUBSCRIBE_FAILED_EVENT) == []

    @pytest.mark.parametrize("code", [10182, 10167, 2104])
    def test_a_code_the_adapter_answers_or_a_warning_is_no_failure(self, ib, code):
        """10182 (disconnected) ends in another reconnect and resubscribe;
        warnings (``WARNING_CODES``, 2100-2199) refuse nothing."""
        self._resubscribed(ib)

        ib.ib_error(code)

        assert getattr(ib.client, FAILURES_ATTRIBUTE) == []

    def test_the_adapter_still_handles_the_error_itself(self, ib):
        """Delegated unchanged: the stock 10182 branch still clears the
        connected flag, so the adapter's own reconnect logic is untouched."""
        self._resubscribed(ib)
        ib.client._is_ib_connected.set()

        ib.ib_error(10182)

        assert not ib.client._is_ib_connected.is_set()

    def test_the_handler_is_the_instance_attribute(self, ib):
        install_bar_resubscribe_fix(ib.client)

        assert ERROR_HANDLER_ATTRIBUTE in vars(ib.client)


class TestTheRecordNames:
    def test_the_pinned_names_are_exactly_these(self):
        assert set(EMITTED_BAR_FEED_EVENTS) == {
            RESUBSCRIBED_EVENT,
            RESUBSCRIBE_FAILED_EVENT,
            CATCH_UP_EVENT,
            CATCH_UP_ORDER_WITHHELD_EVENT,
        }
        assert len(EMITTED_BAR_FEED_EVENTS) == 4


class TestWiring:
    def test_the_exec_factory_installs_it_on_the_shared_client(self, monkeypatch):
        """D-A: data and exec share one ``InteractiveBrokersClient`` (F12), so
        the exec factory's ``create`` installs it there, beside the two exec
        shims, inside ``node.build()`` and before any ``subscribe_bars``."""
        installed = []
        shared = SimpleNamespace(subscribe_historical_bars=lambda **kwargs: None)
        exec_client = SimpleNamespace(_order_avg_prices={}, _client=shared)
        monkeypatch.setattr(StockFactory, "create", staticmethod(lambda **kwargs: exec_client))
        monkeypatch.setattr(
            live_node_builder, "install_position_report_suppression", lambda client: None
        )
        monkeypatch.setattr(live_node_builder, "install_bar_resubscribe_fix", installed.append)

        built = live_node_builder.InteractiveBrokersLiveExecClientFactory.create()

        assert built is exec_client
        assert installed == [shared]

    def test_data_and_exec_resolve_to_the_one_client_the_shim_patches(self):
        """Code review P7: D-A's premise. ``get_cached_ib_client`` keys on
        ``(host, port, client_id)`` (``factories.py:112-127``), and a dockerized
        gateway replaces host and port. Were the two configs ever to resolve to
        different keys, the data client would keep the stock method (the defect
        back) and ``BarFeedWatch`` would read the exec client's empty failures."""
        from nautilus_trader.adapters.interactive_brokers.common import IB

        from src.core.live_node_builder import build_trading_node_config
        from tests.component.core.test_live_node_builder import _settings

        config = build_trading_node_config(_settings(), trader_id="PAPER-a1b2c3d4")
        data, execution = config.data_clients[IB], config.exec_clients[IB]

        def key(client):
            return (client.ibg_host, client.ibg_port, client.ibg_client_id)

        assert key(data) == key(execution)
        assert data.dockerized_gateway is None and execution.dockerized_gateway is None


_LOG_METHODS = {"debug", "info", "warning", "error", "exception"}


def _record_names_in_code() -> set[str]:
    """Every record name a log call in the two modules passes, read off the AST
    — catches a name emitted but never added to the tuple. Any namespace, and
    an ``event=`` keyword too (code review P3): a record outside ``bars.*``
    must not escape the pin and the NFR26 scan."""
    from src.core import live_bar_resubscribe, live_catch_up_gate

    names: set[str] = set()
    for module in (live_bar_resubscribe, live_catch_up_gate):
        tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS
            ):
                continue
            events = [kw.value for kw in node.keywords if kw.arg == "event"]
            for argument in node.args[:1] + events:
                names.add(_record_name(module, argument))
    return names


def _record_name(module, argument: ast.expr) -> str:
    """The string a log call's event argument names; loud when it is not one."""
    if isinstance(argument, ast.Name):
        value = getattr(module, argument.id, None)
    elif isinstance(argument, ast.Constant):
        value = argument.value
    else:
        value = None
    assert isinstance(value, str), f"unreadable record name in {module.__name__}"
    return value


def _drive_every_record() -> list[dict]:
    """Every path that emits a pinned record.

    The account is on hand only where a third party could put it there: the
    raised exception's message and IB's refusal text, both of which the shim
    must never copy, so the scan can fail for ``bars.resubscribe_failed``. The
    other three take no account-bearing input at all (a bar type, timestamps, a
    count); for them the scan is a regression guard, not a probe (code review
    P4)."""
    import structlog

    from src.core.live_catch_up_gate import CatchUpGate

    harness = _Client()
    try:
        with capture_logs() as logs:
            install_bar_resubscribe_fix(harness.client)
            harness.subscribe()
            harness.resubscribe_all()

            def _refuse(**kwargs):
                raise ConnectionResetError(f"socket closed for {RAW_ACCOUNT}")

            harness.client._eclient.reqHistoricalData = _refuse
            harness.resubscribe_all()
            harness.ib_error(162, f"Historical Market Data Service error: {RAW_ACCOUNT}")

            now = [0]
            gate = CatchUpGate(
                SimpleNamespace(submission_withheld=False),
                clock=lambda: now[0],
                log=structlog.get_logger("test").bind(session_id="s-1"),
            )
            minute = 60_000_000_000
            stale = SimpleNamespace(bar_type=BAR_TYPE, ts_event=0)
            now[0] = 10 * minute
            gate.note_bar(stale)
            assert gate.submission_withheld is True
            fresh = SimpleNamespace(bar_type=BAR_TYPE, ts_event=9 * minute)
            gate.note_bar(fresh)
    finally:
        harness.close()
    return logs


class TestEveryEmittedRecordIsPinned:
    """CLAUDE.md, membership-pinned lists: pinned against the code both ways."""

    def test_no_record_is_emitted_that_the_tuple_does_not_name(self):
        assert _record_names_in_code() == set(EMITTED_BAR_FEED_EVENTS)

    def test_every_named_record_is_actually_emitted(self):
        emitted = {entry["event"] for entry in _drive_every_record()}

        assert set(EMITTED_BAR_FEED_EVENTS) <= emitted

    @pytest.mark.parametrize("record_name", EMITTED_BAR_FEED_EVENTS)
    def test_nfr26_no_record_carries_the_account(self, record_name):
        records = [e for e in _drive_every_record() if e["event"] == record_name]

        assert records, f"{record_name} was not emitted by the scenario"
        for record in records:
            assert RAW_ACCOUNT not in repr(record), record


class _SpyReconciler:
    def __init__(self) -> None:
        self.ticks = 0
        self.withheld = object()

    async def on_tick(self) -> None:
        self.ticks += 1


def _node_with_ib_client(ib_client) -> SimpleNamespace:
    """What ``find_ib_exec_client`` walks: ``kernel.exec_engine._clients``."""
    exec_client = SimpleNamespace(_client=ib_client)
    engine = SimpleNamespace(_clients={"INTERACTIVE_BROKERS": exec_client})
    return SimpleNamespace(kernel=SimpleNamespace(exec_engine=engine))


class TestTheWatch:
    """PO ruling R2: checked **before** the reconciler, so a clean reconnect
    cycle can never re-grant trading to a session that cannot hear a price."""

    @pytest.mark.acceptance_criterion("5.9b", AC_5_9B, SOURCE)
    def test_a_noted_failure_stops_before_the_reconciler_runs(self):
        inner = _SpyReconciler()
        failed = SimpleNamespace(**{FAILURES_ATTRIBUTE: [(str(BAR_TYPE), "TypeError")]})
        watch = BarFeedWatch(inner, _node_with_ib_client(failed))

        with pytest.raises(BarFeedLostError) as caught:
            asyncio.run(watch.on_tick())

        assert inner.ticks == 0
        assert str(BAR_TYPE) in str(caught.value) and "TypeError" in str(caught.value)

    def test_no_failure_runs_the_reconciler(self):
        inner = _SpyReconciler()
        healthy = SimpleNamespace(**{FAILURES_ATTRIBUTE: []})
        watch = BarFeedWatch(inner, _node_with_ib_client(healthy))

        asyncio.run(watch.on_tick())

        assert inner.ticks == 1

    def test_a_node_without_an_ib_client_runs_the_reconciler(self):
        inner = _SpyReconciler()
        watch = BarFeedWatch(inner, SimpleNamespace(kernel=SimpleNamespace(exec_engine=object())))

        asyncio.run(watch.on_tick())

        assert inner.ticks == 1

    def test_the_withheld_ledger_passes_through(self):
        """The steady state reads ``reconciler.withheld`` (Story 5.8's ledger)."""
        inner = _SpyReconciler()
        watch = BarFeedWatch(inner, SimpleNamespace())

        assert watch.withheld is inner.withheld
