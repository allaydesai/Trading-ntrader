"""Startup reconciliation against a real ``LiveExecutionEngine`` (Story 4.2, AC #2, #3).

Component tier: a real message bus, ``Cache``, ``Portfolio`` and
``LiveExecutionEngine``, with an IB-shaped NETTING client standing in for the
broker — never a ``TradingNode`` (the C-logging guard below enforces it).

The client reproduces the two IB adapter behaviours the story is built around
(``adapters/interactive_brokers/execution.py``, measured Task 1):

- ``generate_order_status_reports`` **fabricates a FILLED report per broker
  position**, keyed ``client_order_id = instrument id`` (F4);
- ``generate_position_status_reports`` **ignores its instrument filter and skips
  zero quantities**, so a flat instrument never gets a report (F3).

Two groups of tests. The *proofs* drive ``reconcile_at_startup`` and assert the
cache ends broker-ward (AC #3) or the start is refused (D-D). The *canaries*
pin what the framework itself does — each fails **by name** on an upgrade that
changes it, because each is a premise one of the story's decisions rests on.
"""

import ast
import asyncio
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock, is_logging_initialized
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import OrderStatusReport, PositionStatusReport
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import (
    AccountType,
    OmsType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TraderId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from structlog.testing import capture_logs

from src.core import live_startup_reconcile
from src.core.live_node_builder import build_trading_node_config
from src.core.live_startup_reconcile import (
    BROKER_WARD_SETTINGS,
    DISCREPANCY_EVENT,
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
    cached_positions,
    reconcile_at_startup,
    require_broker_ward_reconciliation,
)
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from tests.component.core.test_live_node_builder import _settings as _ibkr_settings

pytestmark = pytest.mark.component

NVDA = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
TRADER = TraderId("PAPER-0e8f1c2a")
ACCOUNT = AccountId("INTERACTIVE_BROKERS-DU4076626")
STRATEGY = StrategyId("SMACrossover-000")
AT = datetime(2026, 9, 27, 13, 25, tzinfo=UTC)


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


class _IBShapedClient(LiveExecutionClient):
    """NETTING, registered as the IB exec client (``find_ib_exec_client``'s key)."""

    def __init__(self, loop, msgbus, cache, clock, broker: dict) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId("INTERACTIVE_BROKERS"),
            venue=Venue("INTERACTIVE_BROKERS"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            instrument_provider=InstrumentProvider(),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        self._set_account_id(ACCOUNT)
        #: ``{InstrumentId: (signed quantity, average price)}`` — the broker.
        self.broker = broker
        self.open_order_reports: list[OrderStatusReport] = []
        self.position_calls: list = []

    async def _connect(self) -> None:
        pass

    async def _disconnect(self) -> None:
        pass

    async def generate_order_status_report(self, command):
        return None

    async def generate_order_status_reports(self, command):
        now = self._clock.timestamp_ns()
        reports = []
        for instrument_id, (qty, avg) in self.broker.items():
            if qty == 0:
                continue
            quantity = Quantity.from_str(str(abs(qty)))
            reports.append(
                OrderStatusReport(
                    account_id=ACCOUNT,
                    instrument_id=instrument_id,
                    venue_order_id=VenueOrderId(instrument_id.value),
                    order_side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.FOK,
                    order_status=OrderStatus.FILLED,
                    quantity=quantity,
                    filled_qty=quantity,
                    avg_px=Decimal(str(avg)),
                    report_id=UUID4(),
                    ts_accepted=now,
                    ts_last=now,
                    ts_init=now,
                    client_order_id=ClientOrderId(instrument_id.value),
                )
            )
        return reports + list(self.open_order_reports)

    async def generate_fill_reports(self, command):
        return []

    async def generate_position_status_reports(self, command):
        self.position_calls.append(getattr(command, "instrument_id", None))
        now = self._clock.timestamp_ns()
        return [
            PositionStatusReport(
                account_id=ACCOUNT,
                instrument_id=instrument_id,
                position_side=PositionSide.LONG if qty > 0 else PositionSide.SHORT,
                quantity=Quantity.from_str(str(abs(qty))),
                report_id=UUID4(),
                ts_last=now,
                ts_init=now,
                avg_px_open=Price.from_str(f"{avg:.2f}"),
            )
            for instrument_id, (qty, avg) in self.broker.items()
            if qty != 0
        ]


#: Nautilus's own default for the switch Story 4.5 turns on (D-A). The framework
#: canaries below pass it explicitly: they pin what the framework does, which
#: the session no longer runs with.
NAUTILUS_DEFAULT_FILTER = {"filter_unclaimed_external_orders": False}


class _Harness:
    def __init__(self, broker: dict | None = None, **engine_config) -> None:
        # The session's own value (Story 4.5, D-A) unless a test says otherwise:
        # the `reconcile` phase refuses an engine that imports the broker twice.
        engine_config.setdefault("filter_unclaimed_external_orders", True)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        clock = TestClock()
        msgbus = MessageBus(trader_id=TRADER, clock=clock)
        self.cache = Cache()
        self.cache.add_instrument(NVDA)
        self.cache.add_instrument(AAPL)
        self.portfolio = Portfolio(msgbus, self.cache, clock)
        self.engine = LiveExecutionEngine(
            loop=self.loop,
            msgbus=msgbus,
            cache=self.cache,
            clock=clock,
            config=LiveExecEngineConfig(**engine_config),
        )
        self.client = _IBShapedClient(self.loop, msgbus, self.cache, clock, broker or {})
        self.engine.register_client(self.client)
        self.engine.register_default_client(self.client)
        self.portfolio.update_account(TestEventStubs.margin_account_state(account_id=ACCOUNT))
        self.engine.start()
        self.node = SimpleNamespace(
            cache=self.cache, kernel=SimpleNamespace(exec_engine=self.engine)
        )

    def close(self) -> None:
        self.engine.stop()
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.close()
        asyncio.set_event_loop(None)

    def seed(self, instrument, qty: int, strategy_id: StrategyId = STRATEGY, px: str = "100.00"):
        """An open position the way a previous process run left it in Redis."""
        order = TestExecStubs.market_order(
            instrument=instrument,
            order_side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
            quantity=Quantity.from_int(abs(qty)),
            trader_id=TRADER,
            strategy_id=strategy_id,
            client_order_id=ClientOrderId(f"O-SEED-{strategy_id}-{instrument.id.symbol}"),
        )
        position_id = PositionId(f"{instrument.id}-{strategy_id}")
        self.cache.add_order(order, position_id)
        order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
        order.apply(TestEventStubs.order_accepted(order, account_id=ACCOUNT))
        fill = TestEventStubs.order_filled(
            order,
            instrument,
            account_id=ACCOUNT,
            position_id=position_id,
            last_px=Price.from_str(px),
        )
        order.apply(fill)
        self.cache.update_order(order)
        self.cache.add_position(Position(instrument, fill), OmsType.NETTING)
        self.portfolio.initialize_positions()

    def native(self) -> bool:
        """Nautilus's own startup pass, as ``start_async`` runs it inside ``node:connect``."""
        return self.loop.run_until_complete(self.engine.reconcile_execution_state(timeout_secs=10))

    def reconcile(self, state: BrokerState, *, local_before=None):
        async def _read(node, *, log):
            return state

        return self.loop.run_until_complete(
            reconcile_at_startup(
                self.node,
                log=structlog.get_logger("test"),
                local_before=local_before,
                read_state=_read,
            )
        )

    def open_positions(self) -> dict[str, Decimal]:
        return {str(p.strategy_id): p.signed_decimal_qty() for p in self.cache.positions_open()}

    def net(self, instrument) -> Decimal:
        return sum(
            (
                p.signed_decimal_qty()
                for p in self.cache.positions_open()
                if p.instrument_id == instrument.id
            ),
            Decimal(0),
        )


@pytest.fixture
def harness():
    made: list[_Harness] = []

    def _make(broker=None, **engine_config) -> _Harness:
        made.append(_Harness(broker, **engine_config))
        return made[-1]

    yield _make
    for each in made:
        each.close()


def _state(*held: tuple[InstrumentId, str, str | None]) -> BrokerState:
    return BrokerState(
        account="***626",
        positions=tuple(
            BrokerPosition(
                instrument_id=str(instrument_id),
                quantity=Decimal(quantity),
                average_price=None if price is None else Decimal(price),
                con_id=4815747,
                symbol=instrument_id.symbol.value,
            )
            for instrument_id, quantity, price in held
        ),
        cash=(CashBalance(currency="USD", total_cash=Decimal("100000")),),
        retrieved_at=AT,
    )


def _session_engine_config() -> dict:
    """Every field the session's builder passes to ``LiveExecEngineConfig``."""
    built = build_trading_node_config(_ibkr_settings(), trader_id=str(TRADER)).exec_engine
    fields = (
        "reconciliation",
        "inflight_check_interval_ms",
        "inflight_check_threshold_ms",
        "inflight_check_retries",
        "open_check_interval_secs",
        "filter_unclaimed_external_orders",
    )
    return {name: getattr(built, name) for name in fields}


def _working_report(venue_order_id: str, client_order_id: str) -> OrderStatusReport:
    """The broker's view of an ``ACCEPTED`` AAPL limit order."""
    return OrderStatusReport(
        account_id=ACCOUNT,
        instrument_id=AAPL.id,
        venue_order_id=VenueOrderId(venue_order_id),
        order_side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        order_status=OrderStatus.ACCEPTED,
        price=Price.from_str("150.00"),
        quantity=Quantity.from_int(3),
        filled_qty=Quantity.from_int(0),
        report_id=UUID4(),
        ts_accepted=0,
        ts_last=0,
        ts_init=0,
        client_order_id=ClientOrderId(client_order_id),
    )


def _restore_working_order(h: "_Harness", client_order_id: str, venue_order_id: str):
    """A strategy's ``ACCEPTED`` limit order, pre-loaded the way
    ``NautilusKernel.__init__`` restores it from Redis."""
    order = TestExecStubs.limit_order(
        instrument=AAPL,
        order_side=OrderSide.BUY,
        price=Price.from_str("150.00"),
        quantity=Quantity.from_int(3),
        time_in_force=TimeInForce.DAY,
        trader_id=TRADER,
        strategy_id=STRATEGY,
        client_order_id=ClientOrderId(client_order_id),
    )
    h.cache.add_order(order)
    order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
    order.apply(
        TestEventStubs.order_accepted(
            order, account_id=ACCOUNT, venue_order_id=VenueOrderId(venue_order_id)
        )
    )
    h.cache.update_order(order)
    return order


class TestTheBrokersViewWins:
    """AC #3 — the cache ends at the broker's view, through the framework."""

    def test_a_stale_synthetic_position_the_broker_closed_is_overwritten(self, harness):
        """(a): an ``EXTERNAL +4`` a previous run imported; the broker has
        since closed it (a manual TWS close). The framework's pass leaves it
        (no report for a flat instrument); the phase corrects it."""
        h = harness()
        h.seed(AAPL, 4, StrategyId("EXTERNAL"))
        assert h.native() is True
        assert h.net(AAPL) == 4, "premise: the framework's own pass left the stale position"

        with capture_logs() as logs:
            result = h.reconcile(_state())

        assert h.net(AAPL) == 0
        assert h.portfolio.net_position(AAPL.id) == 0
        (row,) = result.reconcile_resolved
        assert row.instrument_id == str(AAPL.id)
        assert [e["resolution"] for e in logs if e["event"] == DISCREPANCY_EVENT] == ["broker"]
        assert [e["event"] for e in logs if e["event"] == OK_EVENT] == [OK_EVENT]

    def test_a_broker_position_the_cache_lacks_is_hydrated_at_the_brokers_price(self, harness):
        """(b): measured 1.2 — without the broker's average the correcting fill
        is priced 0; the phase passes it."""
        h = harness()

        h.reconcile(_state((NVDA.id, "5", "101.25")))

        assert h.net(NVDA) == 5
        (position,) = h.cache.positions_open()
        assert Decimal(str(position.avg_px_open)) == Decimal("101.25")

    def test_what_the_frameworks_own_pass_corrected_is_named(self, harness):
        """(c): a fresh cache and a broker holding +10 — Nautilus imports it
        inside ``node:connect`` (as ``INTERNAL-DIFF`` under the session's
        filter since Story 4.5; as ``EXTERNAL`` under its own default); the
        snapshot taken before that makes it a ``resolution="framework"``
        record instead of silence."""
        h = harness({NVDA.id: (10, 100.0)})
        before = cached_positions(h.cache)
        assert h.native() is True

        with capture_logs() as logs:
            result = h.reconcile(_state((NVDA.id, "10", "100")), local_before=before)

        (row,) = result.framework_resolved
        assert (row.local_quantity, row.broker_quantity) == (Decimal("0"), Decimal("10"))
        records = [e for e in logs if e["event"] == DISCREPANCY_EVENT]
        assert [r["resolution"] for r in records] == ["framework"]

    def test_never_the_reverse_a_contradicted_strategy_is_refused_and_left_alone(self, harness):
        """(d), the p7-fill-0901 shape (D-D, PO ruling A): the broker is flat,
        the strategy's own ``+22`` survives the framework's pass. The phase
        neither trusts it nor rewrites it — it refuses, writing nothing."""
        h = harness()
        h.seed(NVDA, 22)
        assert h.native() is True

        with pytest.raises(ReconciliationFailedError) as caught:
            h.reconcile(_state())

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert h.open_positions() == {str(STRATEGY): Decimal("22")}, "the phase wrote the cache"

    def test_a_normal_mid_position_restart_passes(self, harness):
        """Scenario A: strategy +10 cached, broker +10. Under the session's own
        config (Story 4.5, D-A) the framework imports nothing beside it — the
        triple measured at 1.4A is gone — and the phase passes clean."""
        h = harness({NVDA.id: (10, 100.0)})
        h.seed(NVDA, 10)
        assert h.native() is True

        result = h.reconcile(_state((NVDA.id, "10", "100")))

        assert result.reconcile_resolved == () and result.synthetic_positions == 0
        assert h.open_positions() == {str(STRATEGY): Decimal("10")}

    def test_under_nautilus_defaults_the_same_restart_leaves_the_triple(self, harness):
        """The 1.4A measurement, kept as a pin of the framework's own default:
        ``EXTERNAL +10 / INTERNAL-DIFF −10`` beside the strategy, net correct."""
        h = harness({NVDA.id: (10, 100.0)}, **NAUTILUS_DEFAULT_FILTER)
        h.seed(NVDA, 10)

        assert h.native() is True

        assert h.open_positions() == {
            str(STRATEGY): Decimal("10"),
            "EXTERNAL": Decimal("10"),
            "INTERNAL-DIFF": Decimal("-10"),
        }


class TestTheFrameworkLoadsWorkingOrdersAndPositions:
    """AC #2 — characterization of the framework behaviour AR25 relies on."""

    def test_an_open_order_and_a_position_the_broker_reports_are_in_the_cache(self, harness):
        """Under Nautilus's default: an order the cache never knew is imported."""
        h = harness({NVDA.id: (10, 100.0)}, **NAUTILUS_DEFAULT_FILTER)
        h.client.open_order_reports.append(_working_report("IB-77", "O-20260926-000000-000-001-7"))

        assert h.native() is True

        assert [str(o.client_order_id) for o in h.cache.orders_open()] == [
            "O-20260926-000000-000-001-7"
        ]
        assert h.net(NVDA) == 10

    def test_under_the_sessions_filter_an_unknown_working_order_is_not_imported(self, harness):
        """Story 4.5 (D-A)'s disclosed trade-off, pinned: a working order the
        cache never knew (a manual TWS order, or a cache lost) is not imported
        at startup — it belongs to no strategy of this session either way. The
        position the broker reports still is. A strategy's *own* working order,
        restored from Redis, is untouched (the AR25 test below)."""
        h = harness({NVDA.id: (10, 100.0)})
        h.client.open_order_reports.append(_working_report("IB-77", "O-20260926-000000-000-001-7"))

        assert h.native() is True

        assert h.cache.orders_open() == []
        assert h.net(NVDA) == 10

    def test_the_phase_counts_the_working_orders_it_found(self, harness):
        """A strategy's own working order restored from the cache is counted."""
        h = harness()
        _restore_working_order(h, "O-20260926-000000-000-001-8", "IB-78")
        h.client.open_order_reports.append(_working_report("IB-78", "O-20260926-000000-000-001-8"))
        assert h.native() is True

        assert h.reconcile(_state()).open_orders == 1

    def test_a_strategy_working_order_restored_from_redis_survives_the_pass_still_open(
        self, harness
    ):
        """AR25: a strategy's ``ACCEPTED`` limit order, pre-loaded in the cache
        the way ``NautilusKernel.__init__`` restores it from Redis, and reported
        open by the broker, is still open and still the strategy's after the
        framework's own pass — reconciliation does not orphan it to ``EXTERNAL``."""
        h = harness()
        order = TestExecStubs.limit_order(
            instrument=AAPL,
            order_side=OrderSide.BUY,
            price=Price.from_str("150.00"),
            quantity=Quantity.from_int(3),
            time_in_force=TimeInForce.DAY,
            trader_id=TRADER,
            strategy_id=STRATEGY,
            client_order_id=ClientOrderId("O-20260926-000000-000-001-9"),
        )
        h.cache.add_order(order)
        order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
        order.apply(
            TestEventStubs.order_accepted(
                order, account_id=ACCOUNT, venue_order_id=VenueOrderId("IB-79")
            )
        )
        h.cache.update_order(order)
        h.client.open_order_reports.append(
            OrderStatusReport(
                account_id=ACCOUNT,
                instrument_id=AAPL.id,
                venue_order_id=VenueOrderId("IB-79"),
                order_side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.DAY,
                order_status=OrderStatus.ACCEPTED,
                price=Price.from_str("150.00"),
                quantity=Quantity.from_int(3),
                filled_qty=Quantity.from_int(0),
                report_id=UUID4(),
                ts_accepted=0,
                ts_last=0,
                ts_init=0,
                client_order_id=order.client_order_id,
            )
        )

        assert h.native() is True

        (survivor,) = h.cache.orders_open()
        assert survivor.client_order_id == order.client_order_id
        assert survivor.strategy_id == STRATEGY
        assert survivor.status == OrderStatus.ACCEPTED
        assert survivor.venue_order_id == VenueOrderId("IB-79")
        assert h.cache.orders_open(strategy_id=STRATEGY) == [survivor]

    def test_the_built_node_config_loads_the_cache(self, monkeypatch):
        """AC #2's new pin: the working orders and positions AR25 relies on
        reach the engine only because it loads the cache at start. It is
        Nautilus's default today, not a value the builder passes — so this
        pins the built config, and an upgrade or edit that turns it off goes
        red here rather than silently starting every session on an empty cache."""
        for name in ("IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH", "IBKR_MARKET_DATA_LINES"):
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(name.lower(), raising=False)

        cfg = build_trading_node_config(_ibkr_settings(), trader_id=str(TRADER))

        assert cfg.exec_engine.load_cache is True
        assert cfg.exec_engine.reconciliation is True

    def test_the_real_engines_defaults_let_the_broker_win(self, harness):
        """Nautilus's defaults plus the one switch the session departs on (D-A)."""
        require_broker_ward_reconciliation(harness().engine)

    def test_the_enforced_settings_are_pinned_as_an_exact_set(self):
        """Story 4.5 code review ("double pins"): membership changes only
        deliberately, here and in the constant together (CLAUDE.md)."""
        assert dict(BROKER_WARD_SETTINGS) == {
            "reconciliation": True,
            "generate_missing_orders": True,
            "filter_position_reports": False,
            "filter_unclaimed_external_orders": True,
        }
        assert len(BROKER_WARD_SETTINGS) == 4, "a setting is listed twice"

    def test_every_enforced_setting_is_a_real_attribute_of_the_sessions_engine(self, harness):
        """The other direction: each name is read off a real running
        ``LiveExecutionEngine`` built with the session's own config, not a
        double — a misspelt name would otherwise refuse every start."""
        engine = harness(**_session_engine_config()).engine

        for name, expected in BROKER_WARD_SETTINGS:
            assert getattr(engine, name) is expected, name

    def test_a_real_engine_that_imports_the_broker_twice_is_refused(self, harness):
        """Story 4.5 (D-A): Nautilus's own default leaves the filter off."""
        h = harness(**NAUTILUS_DEFAULT_FILTER)

        with pytest.raises(ReconciliationFailedError) as caught:
            h.reconcile(_state())

        assert caught.value.reason is ReconciliationFailure.FRAMEWORK_RECONCILIATION_DISABLED
        assert "filter_unclaimed_external_orders" in str(caught.value)

    def test_a_real_engine_with_reconciliation_off_is_refused(self, harness):
        h = harness(reconciliation=False)

        with pytest.raises(ReconciliationFailedError) as caught:
            h.reconcile(_state())

        assert caught.value.reason is ReconciliationFailure.FRAMEWORK_RECONCILIATION_DISABLED


class TestFrameworkCanaries:
    """Premises the story's decisions rest on. Each fails by name on an upgrade."""

    def test_f6_a_correction_never_lands_in_a_strategys_own_position(self, harness):
        """D-D's premise: a flat report against strategy ``+22`` leaves the
        ``+22`` and opens ``INTERNAL-DIFF −22``."""
        h = harness()
        h.seed(NVDA, 22)

        assert h.engine.reconcile_execution_report(
            PositionStatusReport.create_flat(ACCOUNT, NVDA.id, NVDA.size_precision, 0)
        )

        assert h.open_positions() == {str(STRATEGY): Decimal("22"), "INTERNAL-DIFF": Decimal("-22")}

    def test_f3_the_frameworks_pass_leaves_a_phantom_on_a_flat_broker(self, harness):
        """The p7-fill-0901 mechanism: success reported, phantom untouched, and
        no position report ever requested for the phantom's instrument."""
        h = harness()
        h.seed(NVDA, 22)

        assert h.native() is True

        assert h.open_positions() == {str(STRATEGY): Decimal("22")}
        assert h.client.position_calls == [None], "the per-cached-position sweep now fires"

    def test_f4_a_broker_position_is_imported_as_an_external_order(self, harness):
        h = harness({NVDA.id: (10, 100.0)}, **NAUTILUS_DEFAULT_FILTER)

        assert h.native() is True

        assert h.open_positions() == {"EXTERNAL": Decimal("10")}
        assert h.cache.order(ClientOrderId(str(NVDA.id))) is not None

    def test_f8_a_filtered_instrument_returns_true_having_done_nothing(self, harness):
        h = harness(reconciliation_instrument_ids=[AAPL.id])
        h.seed(NVDA, 4, StrategyId("EXTERNAL"))

        assert h.engine.reconcile_execution_report(
            PositionStatusReport.create_flat(ACCOUNT, NVDA.id, NVDA.size_precision, 0)
        )

        assert h.net(NVDA) == 4

    def test_f9_the_settings_the_phase_enforces_exist_under_these_names(self, harness):
        engine = harness().engine

        assert engine.reconciliation is True
        assert engine.generate_missing_orders is True
        assert engine.filter_position_reports is False
        assert engine.reconciliation_instrument_ids == []
        assert engine.filter_unclaimed_external_orders is True


def _engine_method_calls(source: str) -> set[str]:
    """Every method called on the node's execution engine in ``source``.

    The engine is reached as ``<x>.kernel.exec_engine``. It is followed through
    local aliases (``engine = node.kernel.exec_engine``, walrus included) and into
    the parameters of this module's own functions it is passed to, to a fixed
    point — so restructuring *where* the engine is used does not blind the scan.
    A name is an alias module-wide once bound to the engine anywhere: over-wide
    by design, since a false positive fails loudly and a false negative is
    silent. Attribute *reads* (``getattr(engine, "reconciliation")``) are not
    calls; a call through ``getattr(engine, ...)(...)`` is, recorded as
    ``"<getattr>"``.
    """
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    aliases: set[str] = set()

    def is_engine(expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in aliases
        return (
            isinstance(expr, ast.Attribute)
            and expr.attr == "exec_engine"
            and isinstance(expr.value, ast.Attribute)
            and expr.value.attr == "kernel"
        )

    def bind(target: ast.expr) -> bool:
        if isinstance(target, ast.Name) and target.id not in aliases:
            aliases.add(target.id)
            return True
        return False

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_engine(node.value):
                for target in node.targets:
                    changed |= bind(target)
            elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
                if is_engine(node.value):
                    changed |= bind(node.target)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                callee = functions.get(node.func.id)
                if callee is None:
                    continue
                params = [*callee.args.posonlyargs, *callee.args.args]
                for param, arg in zip(params, node.args, strict=False):
                    if is_engine(arg):
                        changed |= bind(ast.Name(id=param.arg))
                for keyword in node.keywords:
                    if keyword.arg is not None and is_engine(keyword.value):
                        changed |= bind(ast.Name(id=keyword.arg))

    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and is_engine(func.value):
            called.add(func.attr)
        elif (
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "getattr"
            and func.args
            and is_engine(func.args[0])
        ):
            called.add("<getattr>")
    return called


class TestTheOnlyEngineMutationIsTheFrameworksOwnEntryPoint:
    """AC #2 — our code reconciles *through* the framework, never around it:
    the one method ``live_startup_reconcile`` calls on the execution engine is
    ``reconcile_execution_report``. The call-recording spy proves it for the
    paths a test drives; this scan proves it for every path in the source."""

    def test_reconcile_execution_report_is_the_only_engine_method_called(self):
        source = Path(live_startup_reconcile.__file__).read_text()

        assert _engine_method_calls(source) == {"reconcile_execution_report"}

    @pytest.mark.parametrize(
        ("snippet", "expected"),
        [
            (
                "def f(node):\n    node.kernel.exec_engine.reconcile_execution_state()\n",
                {"reconcile_execution_state"},
            ),
            (
                "def f(node):\n    engine = node.kernel.exec_engine\n    engine.purge_cache()\n",
                {"purge_cache"},
            ),
            (
                "def g(e):\n    e.cancel_order(1)\ndef f(node):\n    g(node.kernel.exec_engine)\n",
                {"cancel_order"},
            ),
            (
                "def g(*, e):\n    e.load_cache()\n"
                "def f(node):\n    x = node.kernel.exec_engine\n    g(e=x)\n",
                {"load_cache"},
            ),
            (
                "def f(node):\n    if (e := node.kernel.exec_engine):\n"
                "        getattr(e, 'purge_order')(1)\n",
                {"<getattr>"},
            ),
            (
                "def f(node):\n    e = node.kernel.exec_engine\n"
                "    return getattr(e, 'reconciliation', None)\n",
                set(),
            ),
        ],
        ids=["direct", "alias", "positional-param", "keyword-param", "walrus-getattr", "read"],
    )
    def test_the_scan_sees_each_way_the_engine_can_be_reached(self, snippet, expected):
        """Non-vacuity: each shape a refactor could move the engine into is seen."""
        assert _engine_method_calls(snippet) == expected


_SHRUNK_REENTRY = textwrap.dedent(
    """
    import asyncio, sys
    sys.path.insert(0, {root!r})
    from tests.component.core.test_live_startup_reconcile_engine import _Harness, NVDA
    h = _Harness({{NVDA.id: (10, 100.0)}}, filter_unclaimed_external_orders={filter})
    assert h.native() is True
    print("FIRST_RESTART_OK", flush=True)
    h.client.broker[NVDA.id] = (4, 100.0)
    h.native()
    print("SECOND_RESTART_RETURNED", flush=True)
    """
)


class TestTheShrunkReEntryAbortCanary:
    """F7, measured Task 1.4S and routed to Story 4.5: a restart after the
    broker's position *shrank* (10 → 4) against the ``EXTERNAL`` order a
    previous restart imported makes Nautilus's own pass generate an
    ``OrderUpdated`` below the filled quantity, and the process aborts on a
    Rust panic — uncatchable, before any phase of ours runs. Pinned in a
    subprocess because it kills the interpreter. When an upgrade fixes it,
    this goes red by name and 4.5's routed item can be closed.
    """

    def test_the_second_restart_aborts_the_process(self):
        """Under Nautilus's own default. Story 4.5's session config is the
        twin in ``test_live_session_resume_engine.py`` (it does not abort)."""
        root = str(Path(__file__).resolve().parents[3])
        result = subprocess.run(
            [sys.executable, "-c", _SHRUNK_REENTRY.format(root=root, filter=False)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert "FIRST_RESTART_OK" in result.stdout, result.stderr[-2000:]
        assert "SECOND_RESTART_RETURNED" not in result.stdout
        assert result.returncode != 0
        assert "raw outside valid range" in result.stderr
