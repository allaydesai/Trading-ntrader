"""The rejection tally against a real engine and a real strategy (Story 3.7).

Component tier: a real ``MessageBus``/``Cache``/``Portfolio``/``ExecutionEngine``
and a real Nautilus ``Strategy``, never a ``TradingNode`` — the ``_engine_stack``
shape ``test_live_trade_recorder.py:1289`` established, because it is the only
fixture that reproduces Nautilus's own event routing (deferred position-event
publication, the strategy's ``handle_event`` dispatch) rather than a
hand-applied approximation.

What this file proves that the unit tier cannot:

- **AC #1(b)** — "the session continues operating and remains eligible to
  trade" is a claim about the *engine*, not about the tally. Submit → reject →
  submit **through the real chain** — ``Strategy.submit_order`` → ``RiskEngine``
  → ``ExecutionEngine`` → an exec-client double registered with the engine —
  and the double's ``submit_order`` count is exactly two. (The 2026-09-21
  review found the first version of this harness calling the double directly,
  so the count it asserted was the test's own; the chain is now real.)
- **AC #1(c)** — a rejection does not trip the suppression predicate, so the
  next wrapped ``submit_order`` calls straight through.
- **AC #2(b)** — Nautilus's own ``Strategy.on_order_rejected`` /
  ``on_order_denied`` are no-ops (``trading/strategy.pyx:507-521``, ``:443``),
  pinned against the installed wheel rather than trusted from the docs.
- **AC #2(c)** — a strategy that *does* override ``on_order_rejected`` and
  raises is contained by Story 2.7's ``handle_event`` wrapper, the wrapper the
  Epic 2 retro told Epic 3 not to tidy away (``epics.md:427-431``).
- **AC #4(a)** — a rejected order publishes no position event, so no trade is
  recorded. Beside a control that *does* record one, so the assertion cannot
  pass vacuously.
- **Decision D-I** — the transcript keeps the venue's reason **verbatim**
  (Story 3.3's AC #3) while the tally's snapshot carries the **redacted** one.
  Both asserted in one test, so the two policies are visibly distinct rather
  than accidentally identical.
"""

import itertools
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import structlog
from nautilus_trader.common.component import (
    LiveClock,
    MessageBus,
    TestClock,
    is_logging_initialized,
)
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.client import ExecutionClient
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, LiquiditySide, OmsType, OrderSide, OrderType
from nautilus_trader.model.events.order import (
    OrderAccepted,
    OrderDenied,
    OrderFilled,
    OrderRejected,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.trading.strategy import Strategy
from structlog.testing import capture_logs

from src.core.live_order_path import (
    DENIED_EVENT,
    ORDER_EVENTS_TOPIC,
    REJECTED_EVENT,
    OrderEventObserver,
    install_order_path,
)
from src.core.live_order_rejections import TALLY_FAILED_EVENT, RejectionTally
from src.core.live_strategy_guard import FAILED_EVENT, StrategyGuard
from src.core.live_trade_recorder import (
    AGGREGATED_EVENT,
    POSITION_EVENTS_TOPIC,
    RECORDER_FAILED_EVENT,
    TradeRecorder,
)

pytestmark = pytest.mark.component

AAPL_EQUITY = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
TRADER_ID = TraderId("TESTER-000")
_COUNTER = itertools.count()

#: The paper account this repo's live transcripts carry. Embedded in a
#: rejection reason below because IB rejection text really can carry it —
#: Story 3.3's escalated NFR26 conflict — so the verbatim/redacted split has
#: something real to be measured on.
PAPER_ACCOUNT = "DU4076626"
MARGIN_REJECTION = f"Order rejected - reason: 201 account {PAPER_ACCOUNT} has insufficient margin"

#: A fixed clock reading, injected into the tally. ``freezegun`` is banned
#: near this code path; an injected ``time_source`` is the sanctioned seam.
T0 = datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_live_order_path.py:91-101``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a "
        "TradingNode."
    )


class _RecordingExecClient(ExecutionClient):
    """Counts what actually reached the venue side.

    The whole of AC #1(b) is an integer: after a rejection, the *next* order
    still leaves. Registered with the real ``ExecutionEngine`` (review
    2026-09-21), so ``submitted`` holds only what the strategy → risk engine →
    execution engine chain handed over — never what a test helper wrote. Each
    submission generates ``OrderSubmitted`` exactly as a real client does
    (the IB adapter's ``_submit_order``), so the order reads ``SUBMITTED``
    through the engine's own apply path.
    """

    def __init__(self, account_id: AccountId, msgbus, cache, clock) -> None:
        # The client id must be the account's issuer (`_set_account_id`
        # asserts it); routing is by `venue`, which is AAPL's. NETTING, not
        # the test kit's `MockExecutionClient` HEDGING default: the engine
        # reads the OMS type off the client, and under HEDGING the control's
        # exit fill opens a second position instead of closing the first.
        super().__init__(
            client_id=ClientId(account_id.get_issuer()),
            venue=AAPL_EQUITY.id.venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=USD,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        self.submitted: list[ClientOrderId] = []
        self._set_account_id(account_id)  # `generate_order_submitted` stamps it

    def _start(self) -> None:
        self._set_connected(True)

    def _stop(self) -> None:
        self._set_connected(False)

    def submit_order(self, command) -> None:
        order = command.order
        self.submitted.append(order.client_order_id)
        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )


def _engine_stack():
    """A real engine, bus, cache, portfolio, risk engine and ``Strategy`` — no
    node — with the recording exec-client double registered for AAPL's venue.
    """
    from nautilus_trader.execution.engine import ExecutionEngine
    from nautilus_trader.portfolio.portfolio import Portfolio
    from nautilus_trader.risk.engine import RiskEngine
    from nautilus_trader.test_kit.stubs.component import TestComponentStubs
    from nautilus_trader.test_kit.stubs.execution import TestExecStubs

    clock = TestClock()
    msgbus = MessageBus(trader_id=TRADER_ID, clock=clock)
    cache = TestComponentStubs.cache()
    portfolio = Portfolio(msgbus=msgbus, cache=cache, clock=clock)
    engine = ExecutionEngine(msgbus=msgbus, cache=cache, clock=clock)
    risk_engine = RiskEngine(portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock)
    cache.add_instrument(AAPL_EQUITY)
    account = TestExecStubs.cash_account()
    cache.add_account(account)
    portfolio.update_account(TestEventStubs.cash_account_state())
    client = _RecordingExecClient(account.id, msgbus, cache, clock)
    engine.register_client(client)
    strategy = Strategy()
    strategy.register(
        trader_id=TRADER_ID, portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock
    )
    risk_engine.start()
    engine.start()
    client.start()
    return msgbus, cache, engine, strategy, account, clock, client


def _tally(account: str | None = None) -> RejectionTally:
    return RejectionTally(
        structlog.get_logger("test").bind(session_id="s1"),
        time_source=lambda: T0,
        account=account,
    )


def _submit(stack, side=OrderSide.BUY, qty=100):
    """Submit one market order the way a signal does: ``Strategy.submit_order``
    → ``RiskEngine`` → ``ExecutionEngine`` → the registered double, which
    generates ``OrderSubmitted`` back through the engine. Returns the order,
    ``SUBMITTED`` by the engine's own apply path — nothing here touches the
    cache or applies an event by hand (review 2026-09-21).
    """
    _, _cache, _engine, strategy, _account, _clock, _client = stack
    order = strategy.order_factory.market(AAPL_EQUITY.id, side, Quantity.from_int(qty))
    strategy.submit_order(order)
    return order


def _reject(stack, order, reason=MARGIN_REJECTION, reconciliation=False):
    """Drive a real ``OrderRejected`` through the real engine.

    Built directly rather than through ``TestEventStubs.order_rejected``,
    which hardcodes ``reason="ORDER_REJECTED"`` (Story 3.3, Hazard 8) — and
    the reason is the whole point of several tests here.
    """
    _, _cache, engine, _strategy, account, clock, _client = stack
    event = OrderRejected(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        account_id=account.id,
        reason=reason,
        event_id=UUID4(),
        ts_event=clock.timestamp_ns(),
        ts_init=clock.timestamp_ns(),
        due_post_only=False,
        reconciliation=reconciliation,
    )
    engine.process(event)
    return event


def _accept(stack, order, reconciliation=False):
    _, _cache, engine, _strategy, account, clock, _client = stack
    event = OrderAccepted(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        venue_order_id=VenueOrderId(f"V-{order.client_order_id}"),
        account_id=account.id,
        event_id=UUID4(),
        ts_event=clock.timestamp_ns(),
        ts_init=clock.timestamp_ns(),
        reconciliation=reconciliation,
    )
    engine.process(event)
    return event


def _fill(stack, order, px="100.00", commission="1.00"):
    _, _cache, engine, _strategy, account, clock, _client = stack
    event = OrderFilled(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        venue_order_id=VenueOrderId(f"V-{order.client_order_id}"),
        account_id=account.id,
        trade_id=TradeId(f"T-{next(_COUNTER)}"),
        position_id=None,
        order_side=order.side,
        order_type=OrderType.MARKET,
        last_qty=order.quantity,
        last_px=Price.from_str(px),
        currency=USD,
        commission=Money(Decimal(commission), USD),
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=clock.timestamp_ns(),
        ts_init=clock.timestamp_ns(),
    )
    clock.advance_time(clock.timestamp_ns() + 1_000_000_000)
    engine.process(event)
    return event


class TestARejectionLeavesTheSessionEligibleToTrade:
    """AC #1(b) and #1(c), against the real engine.

    Story 3.3 and Nautilus already make "the session continues" mechanically
    true; nothing in this story creates it. What is new is that it is now
    *pinned*, so a future change that made a rejection terminal goes red here.
    """

    def _wire(self, stack, *, account=None):
        msgbus = stack[0]
        observer = OrderEventObserver(structlog.get_logger("test"))
        tally = _tally(account)
        msgbus.subscribe(topic=ORDER_EVENTS_TOPIC, handler=observer.handle_order_event)
        msgbus.subscribe(topic=ORDER_EVENTS_TOPIC, handler=tally.handle_order_event)
        return observer, tally

    def test_the_order_after_a_rejection_is_still_submitted(self):
        stack = _engine_stack()
        client = stack[6]  # the exec-client double registered with the engine
        _observer, tally = self._wire(stack)

        first = _submit(stack)
        _reject(stack, first)
        second = _submit(stack)

        assert len(client.submitted) == 2
        assert client.submitted == [first.client_order_id, second.client_order_id]
        # `.status_string()`, never `str(order.status)`: `OrderStatus.__str__`
        # renders the underlying integer (`'5'`), so a string comparison
        # against "SUBMITTED" fails for the right value.
        assert second.status_string() == "SUBMITTED"
        snapshot = tally.pending()
        assert snapshot is not None
        assert (snapshot.rejected, snapshot.consecutive) == (1, 1)

    def test_an_acceptance_of_the_next_order_clears_the_streak(self):
        stack = _engine_stack()
        _observer, tally = self._wire(stack)

        first = _submit(stack)
        _reject(stack, first)
        second = _submit(stack)
        _accept(stack, second)

        snapshot = tally.pending()
        assert snapshot is not None
        assert snapshot.consecutive == 0
        assert snapshot.rejected == 1

    def test_a_reconciliation_acceptance_does_not_clear_the_streak(self):
        """Story 3.4's startup restore replays an ``OrderAccepted`` for an
        order accepted *before* the restart. That is not evidence that orders
        are getting through today (decision D-C).
        """
        stack = _engine_stack()
        _observer, tally = self._wire(stack)

        first = _submit(stack)
        _reject(stack, first)
        second = _submit(stack)
        _accept(stack, second, reconciliation=True)

        snapshot = tally.pending()
        assert snapshot is not None
        assert snapshot.consecutive == 1

    def test_the_transcript_keeps_the_reason_verbatim_and_the_column_redacts_it(self):
        """**Decision D-I, in one test so the two policies cannot silently
        converge.** Story 3.3's AC #3 makes ``venue_reason`` verbatim — never
        reworded, truncated or classified — and this story does not reverse
        that. NFR26 is value-level and IB text can embed the account code, so
        the *column*, which ``live status`` renders, is redacted at the catch
        site instead. Both policies hold, on the same event, at once.
        """
        stack = _engine_stack()
        _observer, tally = self._wire(stack, account=PAPER_ACCOUNT)

        order = _submit(stack)
        with capture_logs() as logs:
            _reject(stack, order)

        rejected = [entry for entry in logs if entry["event"] == REJECTED_EVENT]
        assert len(rejected) == 1
        assert rejected[0]["venue_reason"] == MARGIN_REJECTION
        assert PAPER_ACCOUNT in rejected[0]["venue_reason"]

        snapshot = tally.pending()
        assert snapshot is not None
        assert PAPER_ACCOUNT not in snapshot.last_reason
        assert "***626" in snapshot.last_reason
        assert "insufficient margin" in snapshot.last_reason

    def test_a_denial_is_tallied_and_logged_without_reaching_the_venue(self):
        """The risk engine's local refusal. Same meaning to the operator — the
        strategy asked and nothing was placed — so it extends the same streak.
        """
        stack = _engine_stack()
        msgbus, _cache, engine, _strategy, _account, clock, _client = stack
        _observer, tally = self._wire(stack)

        order = _submit(stack)
        denied = OrderDenied(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            reason="NOTIONAL_EXCEEDS_FREE_BALANCE",
            event_id=UUID4(),
            # Measured against the installed 1.220.0 wheel while writing this
            # test: `OrderDenied.__init__` takes `ts_init` ONLY
            # (`model/events/order.pyx:661-680`) and derives `ts_event` from
            # it — unlike every other order event in this file. The story's
            # Dev Notes said "the same attributes minus `due_post_only`",
            # which is true of the *attributes* and false of the constructor.
            ts_init=clock.timestamp_ns(),
        )
        with capture_logs() as logs:
            msgbus.publish(topic=f"events.order.{order.strategy_id}", msg=denied)

        assert [entry for entry in logs if entry["event"] == DENIED_EVENT] != []
        snapshot = tally.pending()
        assert snapshot is not None
        assert (snapshot.rejected, snapshot.denied, snapshot.consecutive) == (0, 1, 1)
        assert snapshot.last_kind == "denied"

    def test_no_tally_failure_is_ever_recorded_on_the_happy_path(self):
        """The whole handler lives in a ``try``; this pins that the ``try`` is
        not quietly swallowing every event.
        """
        stack = _engine_stack()
        _observer, tally = self._wire(stack)

        with capture_logs() as logs:
            order = _submit(stack)
            _reject(stack, order)
            second = _submit(stack)
            _accept(stack, second)
            _fill(stack, second)

        assert [entry for entry in logs if entry["event"] == TALLY_FAILED_EVENT] == []
        assert tally.pending() is not None


class TestARejectionDoesNotWithholdTheNextSubmission:
    """AC #1(c): the suppression predicate is untouched by a rejection.

    ``install_order_path`` withholds order-creating calls while the connection
    is known lost or unobserved. A rejection is neither, and nothing in this
    story may make it one — that would turn one refused order into a session
    that stops asking, which is the failure the story exists to prevent, one
    layer down.
    """

    class _StubMonitor:
        """The one member ``install_order_path`` duck-types against."""

        def __init__(self) -> None:
            self.submission_withheld = False

    class _SpyStrategy(Strategy):
        """A **Python subclass**, deliberately.

        ⚠️ Measured (and documented in ``live_order_path.py``'s own module
        docstring): a bare ``Strategy`` is a cdef class with no ``__dict__``
        and rejects the instance-level assignment ``install_order_path``
        makes — ``AttributeError: 'Strategy' object attribute 'submit_order'
        is read-only``. Every registered strategy in this repo is a Python
        subclass, so this double matches production; a bare ``Strategy``
        would fail at wrap time for a reason unrelated to this test.
        """

        def __init__(self) -> None:
            super().__init__()
            self.submitted: list[object] = []

        def submit_order(self, order, *args, **kwargs) -> None:
            self.submitted.append(order)

    def _registered(self):
        from nautilus_trader.portfolio.portfolio import Portfolio
        from nautilus_trader.test_kit.stubs.component import TestComponentStubs

        clock = LiveClock()
        msgbus = MessageBus(trader_id=TRADER_ID, clock=clock)
        cache = TestComponentStubs.cache()
        cache.add_instrument(AAPL_EQUITY)
        portfolio = Portfolio(msgbus, cache, clock)
        strategy = self._SpyStrategy()
        strategy.register(TRADER_ID, portfolio, msgbus, cache, clock)
        strategy.start()  # `handle_event` is FSM-gated — see the note above.
        return strategy, msgbus

    def test_the_next_wrapped_submit_calls_straight_through(self):
        strategy, msgbus = self._registered()
        monitor = self._StubMonitor()
        tally = _tally()
        msgbus.subscribe(topic=ORDER_EVENTS_TOPIC, handler=tally.handle_order_event)
        install_order_path(strategy, monitor, structlog.get_logger("test"))

        order = strategy.order_factory.market(AAPL_EQUITY.id, OrderSide.BUY, Quantity.from_int(10))
        with capture_logs() as logs:
            strategy.handle_event(_a_rejection(strategy))
            strategy.submit_order(order)

        assert monitor.submission_withheld is False
        assert strategy.submitted == [order]
        assert [entry for entry in logs if entry["event"] == "order.suppressed"] == []

    def test_the_control_a_withheld_monitor_does_suppress_it(self):
        """The anti-tautology twin: the same wrapper, the same call, a monitor
        that *is* withholding — so the assertion above is testing the
        predicate's value rather than the absence of a wrapper.
        """
        strategy, _msgbus = self._registered()
        monitor = self._StubMonitor()
        monitor.submission_withheld = True
        install_order_path(strategy, monitor, structlog.get_logger("test"))

        order = strategy.order_factory.market(AAPL_EQUITY.id, OrderSide.BUY, Quantity.from_int(10))
        with capture_logs() as logs:
            strategy.submit_order(order)

        assert strategy.submitted == []
        assert [entry for entry in logs if entry["event"] == "order.suppressed"] != []


class TestNautilusOwnHandlersAreNoOps:
    """AC #2(b), pinned against the installed 1.220.0 wheel rather than trusted
    from the docs ("Nautilus docs are not evidence").

    ``Strategy.on_order_rejected`` (``trading/strategy.pyx:507-521``) and
    ``on_order_denied`` (``:443``) are ``# Optionally override in subclass``
    with an empty body, and ``handle_event`` (``:1615``) dispatches
    ``OrderRejected -> on_order_rejected -> on_order_event`` inside its own
    ``try``. So a rejection reaches an un-overriding strategy as a silent
    ``pass``, and its next signal submits a fresh order — *"remains eligible
    to trade"* is already the mechanical truth. This story pins it; it does
    not create it.
    """

    class _Spy(Strategy):
        def __init__(self) -> None:
            super().__init__()
            self.bars: list[object] = []

        def on_bar(self, bar) -> None:
            self.bars.append(bar)

    def _registered(self):
        from nautilus_trader.portfolio.portfolio import Portfolio
        from nautilus_trader.test_kit.stubs.component import TestComponentStubs

        clock = LiveClock()
        msgbus = MessageBus(trader_id=TRADER_ID, clock=clock)
        cache = TestComponentStubs.cache()
        cache.add_instrument(AAPL_EQUITY)
        portfolio = Portfolio(msgbus, cache, clock)
        strategy = self._Spy()
        strategy.register(TRADER_ID, portfolio, msgbus, cache, clock)
        # ⚠️ Measured against the installed 1.220.0 wheel while writing this
        # file: `Strategy.handle_event` returns early unless the component
        # FSM reads RUNNING (`trading/strategy.pyx:1645`,
        # `if self._fsm.state != ComponentState.RUNNING: return`). A
        # registered-but-unstarted strategy therefore swallows every event
        # BEFORE reaching `on_order_rejected` — which would make the
        # containment tests below pass for entirely the wrong reason.
        strategy.start()
        return strategy

    def test_no_built_in_strategy_overrides_either_hook(self):
        """The grep, as an assertion: if a future strategy overrides one, this
        file's containment claims need re-deriving for it.
        """
        import pathlib

        from src.core import strategies as strategies_package

        # Anchored on the package, not the CWD (review 2026-09-21: a relative
        # path scanned nothing from any other directory and passed vacuously),
        # and `custom/` — the git submodule core must not depend on — is
        # outside "built-in".
        root = pathlib.Path(strategies_package.__file__).parent
        sources = [path for path in root.rglob("*.py") if "custom" not in path.parts]
        assert len(sources) >= 3, "the built-in strategies were not found"
        offenders = [
            str(path)
            for path in sources
            if "def on_order_rejected" in path.read_text(encoding="utf-8")
            or "def on_order_denied" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_the_base_hooks_are_empty(self):
        """``# Optionally override in subclass`` with an empty body — so a
        rejection reaches an un-overriding strategy as a silent ``pass``.
        Called directly, because the ``handle_event`` route below is gated on
        the component FSM and would hide an implementation that was not a
        no-op at all.
        """
        strategy = self._registered()
        event = _a_rejection(strategy)

        assert strategy.on_order_rejected(event) is None
        assert strategy.on_order_denied(_a_denial(strategy)) is None

    @pytest.mark.parametrize("event_type", ["rejected", "denied"])
    def test_a_refusal_through_handle_event_leaves_the_strategy_handling_bars(self, event_type):
        """The mechanical truth AC #1 names, pinned: a refusal reaches a
        strategy that overrides neither hook, nothing happens, and the next
        bar still reaches ``on_bar`` — from which the strategy's next signal
        submits a fresh order.
        """
        strategy = self._registered()
        event = _a_rejection(strategy) if event_type == "rejected" else _a_denial(strategy)

        strategy.handle_event(event)  # must not raise
        strategy.handle_bar(_a_bar())

        assert len(strategy.bars) == 1


class TestAStrategyThatRaisesOnARejectionIsContained:
    """AC #2(c) — the wrapper the Epic 2 retro told Epic 3 not to tidy away
    (``epics.md:427-431``), exercised by the event it was kept for.

    Story 2.7's ``StrategyGuard`` wraps ``handle_event`` as well as
    ``handle_bar``, deliberately ahead of need: no strategy in this repo
    overrides ``on_order_rejected`` today. This test is the need arriving. A
    future change to ``GUARDED_HANDLERS`` that dropped ``handle_event`` would
    turn the raise below into Nautilus's silent ``os._exit(1)``, and goes red
    here instead.
    """

    class _Raiser(Strategy):
        def on_order_rejected(self, event) -> None:
            raise RuntimeError(f"boom {PAPER_ACCOUNT}")

    def _registered(self):
        from nautilus_trader.portfolio.portfolio import Portfolio
        from nautilus_trader.test_kit.stubs.component import TestComponentStubs

        clock = LiveClock()
        msgbus = MessageBus(trader_id=TRADER_ID, clock=clock)
        cache = TestComponentStubs.cache()
        cache.add_instrument(AAPL_EQUITY)
        portfolio = Portfolio(msgbus, cache, clock)
        strategy = self._Raiser()
        strategy.register(TRADER_ID, portfolio, msgbus, cache, clock)
        # See `TestNautilusOwnHandlersAreNoOps._registered` — `handle_event`
        # is gated on the FSM reading RUNNING, so an unstarted strategy would
        # make this whole class pass without ever reaching the raise.
        strategy.start()
        return strategy

    def _rejection_for(self, strategy):
        order = strategy.order_factory.market(AAPL_EQUITY.id, OrderSide.BUY, Quantity.from_int(10))
        return OrderRejected(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            account_id=AccountId("INTERACTIVE_BROKERS-DU4076626"),
            reason=MARGIN_REJECTION,
            event_id=UUID4(),
            ts_event=0,
            ts_init=0,
            due_post_only=False,
            reconciliation=False,
        )

    def test_the_raise_becomes_one_contained_failure_record(self):
        strategy = self._registered()
        guard = StrategyGuard(
            log=structlog.get_logger("test"), time_source=lambda: T0, account=PAPER_ACCOUNT
        )
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        with capture_logs() as logs:
            strategy.handle_event(_a_rejection(strategy))  # must not raise

        failed = [entry for entry in logs if entry["event"] == FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["handler"] == "handle_event"
        assert failed[0]["error_type"] == "RuntimeError"

    def test_the_detail_that_reaches_the_column_is_redacted(self):
        """NFR26 on this path too: the message embeds an account id, and the
        column gets the masked form.
        """
        strategy = self._registered()
        guard = StrategyGuard(
            log=structlog.get_logger("test"), time_source=lambda: T0, account=PAPER_ACCOUNT
        )
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        with capture_logs():
            strategy.handle_event(_a_rejection(strategy))

        (failure,) = guard.failures
        assert PAPER_ACCOUNT not in failure.detail
        assert "***626" in failure.detail

    def test_the_guard_latches_so_the_raiser_stops_receiving_events(self):
        strategy = self._registered()
        guard = StrategyGuard(
            log=structlog.get_logger("test"), time_source=lambda: T0, account=PAPER_ACCOUNT
        )
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        with capture_logs() as logs:
            strategy.handle_event(_a_rejection(strategy))
            strategy.handle_event(_a_rejection(strategy))

        assert len([entry for entry in logs if entry["event"] == FAILED_EVENT]) == 1
        assert len(guard.failures) == 1


class TestARejectionIsNotATrade:
    """AC #4(a) — FR24, against the real engine, with a control.

    A rejected order publishes no ``PositionOpened``/``Changed``/``Closed``, so
    the trade recorder is never reached and no trade row can be created. The
    control below fills an order and *does* reach the sink, so the absence
    above cannot pass vacuously against a recorder that was never subscribed.
    """

    def _wire(self, stack):
        msgbus = stack[0]
        cache = stack[1]
        sink_calls: list[object] = []
        position_events: list[object] = []
        recorder = TradeRecorder(cache, structlog.get_logger("test"), sink=sink_calls.append)
        msgbus.subscribe(topic=POSITION_EVENTS_TOPIC, handler=recorder.handle_position_event)
        msgbus.subscribe(topic=POSITION_EVENTS_TOPIC, handler=position_events.append)
        tally = _tally()
        msgbus.subscribe(topic=ORDER_EVENTS_TOPIC, handler=tally.handle_order_event)
        return sink_calls, position_events, tally

    def test_a_rejected_order_publishes_no_position_event_and_records_no_trade(self):
        stack = _engine_stack()
        sink_calls, position_events, tally = self._wire(stack)

        with capture_logs() as logs:
            order = _submit(stack)
            _reject(stack, order)

        assert position_events == []
        assert sink_calls == []
        assert [entry for entry in logs if entry["event"] == AGGREGATED_EVENT] == []
        assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
        assert tally.pending() is not None  # it WAS counted as a refusal

    def test_the_control_an_accepted_and_filled_round_trip_does_record_one(self):
        """Without this, the assertion above passes against a recorder that was
        never wired at all.
        """
        stack = _engine_stack()
        sink_calls, position_events, _tally = self._wire(stack)

        entry = _submit(stack, side=OrderSide.BUY)
        _accept(stack, entry)
        _fill(stack, entry, px="100.00")
        exit_order = _submit(stack, side=OrderSide.SELL)
        _accept(stack, exit_order)
        _fill(stack, exit_order, px="110.00")

        assert position_events != []
        assert len(sink_calls) == 1


class TestTheRecorderCannotBeReachedByAnOrderEvent:
    """AC #4(b) — the structural half, so FR24 does not rest on one scenario.

    Two independent barriers: the recorder's dispatch keys name only position
    events, and its topic pattern cannot match an order topic. Either alone
    would do; both are pinned because each catches a different mutation.
    """

    def test_the_dispatch_keys_are_position_events_only(self):
        from nautilus_trader.test_kit.stubs.component import TestComponentStubs

        recorder = TradeRecorder(TestComponentStubs.cache(), structlog.get_logger("test"))

        assert set(recorder._dispatch) <= {
            "PositionOpened",
            "PositionChanged",
            "PositionClosed",
        }
        assert not any(name.startswith("Order") for name in recorder._dispatch)

    def test_the_position_topic_pattern_does_not_match_an_order_topic(self):
        from fnmatch import fnmatch

        assert fnmatch("events.order.SMACrossover-000", POSITION_EVENTS_TOPIC) is False
        assert fnmatch("events.position.SMACrossover-000", POSITION_EVENTS_TOPIC) is True


def _a_rejection(strategy, reason=MARGIN_REJECTION):
    order = strategy.order_factory.market(AAPL_EQUITY.id, OrderSide.BUY, Quantity.from_int(10))
    return OrderRejected(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        account_id=AccountId("INTERACTIVE_BROKERS-DU4076626"),
        reason=reason,
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
        due_post_only=False,
        reconciliation=False,
    )


def _a_denial(strategy):
    order = strategy.order_factory.market(AAPL_EQUITY.id, OrderSide.BUY, Quantity.from_int(10))
    return OrderDenied(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        reason="NOTIONAL_EXCEEDS_FREE_BALANCE",
        event_id=UUID4(),
        ts_init=0,  # `OrderDenied` takes no `ts_event` — see the note below.
    )


def _a_bar():
    from nautilus_trader.model.data import Bar, BarType

    bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")
    price = Price.from_str("100.00")
    return Bar(
        bar_type=bar_type,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_int(1_000),
        ts_event=0,
        ts_init=60_000_000_000,
    )
