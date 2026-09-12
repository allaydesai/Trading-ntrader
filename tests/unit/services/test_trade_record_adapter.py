"""Unit tests for ``SqlTradeRecord``, the trade sink's SQLAlchemy adapter
(Story 3.6).

Unit tier and no database: the ``get_sync_session`` factory is injected, so
what is under test is the transaction discipline and the routing, not
Postgres. The ``_RecordingFactory`` harness is copied from
``test_session_record_adapter.py:34-52`` (not imported — that directory has
no ``__init__.py``).
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.live_session_record import SessionReclaimedError
from src.core.live_trade_recorder import RecordedTrade
from src.models.trade import TradeBase
from src.services.trade_record import SqlTradeRecord

pytestmark = pytest.mark.unit

SESSION_PK = 42
OWNER_EPOCH = 7


class _RecordingFactory:
    """A ``get_sync_session``-shaped factory that counts enters and exits."""

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self.sessions: list[MagicMock] = []

    @contextmanager
    def __call__(self):
        self.entered += 1
        session = MagicMock(name=f"sync-session-{self.entered}")
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.exited += 1

    @property
    def balanced(self) -> bool:
        return self.entered == self.exited


def _recorded_trade(**overrides) -> RecordedTrade:
    trade = TradeBase(
        instrument_id="AAPL.NASDAQ",
        trade_id="AAPL.NASDAQ-SMACrossover-000",
        venue_order_id="O-1",
        client_order_id="O-2",
        order_side="BUY",
        quantity=Decimal("10.00000000"),
        entry_price=Decimal("100.00000000"),
        exit_price=Decimal("110.00000000"),
        commission_amount=Decimal("1.50000000"),
        commission_currency="USD",
        fees_amount=Decimal("0.00"),
        entry_timestamp=datetime(2026, 9, 12, 14, 0, 0, tzinfo=UTC),
        exit_timestamp=datetime(2026, 9, 12, 14, 5, 0, tzinfo=UTC),
    )
    fields = {
        "trade": trade,
        "profit_loss": Decimal("98.50000000"),
        "profit_pct": Decimal("10.00000000"),
        "holding_period_seconds": 300,
        "position_id": "AAPL.NASDAQ-SMACrossover-000",
        "strategy_id": "SMACrossover-000",
        "fill_count": 2,
        "trade_key": "AAPL.NASDAQ-SMACrossover-000:O-2",
    }
    fields.update(overrides)
    return RecordedTrade(**fields)


@pytest.fixture
def patched(monkeypatch):
    """Replace the repository the adapter builds per call."""
    from src.services import trade_record as adapter_module

    repository = MagicMock()
    repository.stamp_activity_if_owner.return_value = 1
    repository.insert_trade_if_absent.return_value = True
    monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
    return adapter_module, repository


class TestOneFactoryBlockPerCall:
    """Always balanced, even when the repository raises."""

    def test_a_successful_call_opens_and_closes_exactly_one_session(self, patched):
        _, repository = patched
        factory = _RecordingFactory()
        record = SqlTradeRecord(SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=factory)

        record.persist(_recorded_trade())

        assert (factory.entered, factory.exited) == (1, 1)

    def test_a_raising_insert_still_closes_its_session(self, patched):
        _, repository = patched
        repository.insert_trade_if_absent.side_effect = RuntimeError("db went away")
        factory = _RecordingFactory()
        record = SqlTradeRecord(SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=factory)

        with pytest.raises(RuntimeError):
            record.persist(_recorded_trade())

        assert factory.balanced

    def test_a_reclaimed_fence_still_closes_its_session(self, patched):
        _, repository = patched
        repository.stamp_activity_if_owner.return_value = 0
        factory = _RecordingFactory()
        record = SqlTradeRecord(SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=factory)

        with pytest.raises(SessionReclaimedError):
            record.persist(_recorded_trade())

        assert factory.balanced

    def test_invalid_trade_opens_no_factory_block(self, patched):
        """Validate before the transaction — a caller bug is not a DB error."""
        _, repository = patched
        factory = _RecordingFactory()
        record = SqlTradeRecord(SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=factory)
        bad_trade = TradeBase.model_construct(
            instrument_id="AAPL.NASDAQ",
            trade_id="T-1",
            venue_order_id="O-1",
            client_order_id="O-2",
            order_side="BUY",
            quantity=Decimal("-5"),  # invalid: quantity must be > 0
            entry_price=Decimal("100.00"),
            exit_price=Decimal("110.00"),
            commission_amount=None,
            commission_currency=None,
            fees_amount=Decimal("0.00"),
            entry_timestamp=datetime(2026, 9, 12, 14, 0, 0, tzinfo=UTC),
            exit_timestamp=datetime(2026, 9, 12, 14, 5, 0, tzinfo=UTC),
        )

        with pytest.raises(ValueError):
            record.persist(_recorded_trade(trade=bad_trade))

        assert factory.entered == 0


class TestFenceBeforeInsert:
    """The heartbeat fence is checked first; a refusal never reaches the insert."""

    def test_the_fence_runs_before_the_insert(self, patched):
        _, repository = patched
        order: list[str] = []
        repository.stamp_activity_if_owner.side_effect = lambda *a, **k: order.append("fence") or 1
        repository.insert_trade_if_absent.side_effect = (
            lambda *a, **k: order.append("insert") or True
        )
        record = SqlTradeRecord(
            SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=_RecordingFactory()
        )

        record.persist(_recorded_trade())

        assert order == ["fence", "insert"]

    def test_a_reclaimed_fence_never_calls_insert(self, patched):
        _, repository = patched
        repository.stamp_activity_if_owner.return_value = 0
        record = SqlTradeRecord(
            SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=_RecordingFactory()
        )

        with pytest.raises(SessionReclaimedError):
            record.persist(_recorded_trade())

        repository.insert_trade_if_absent.assert_not_called()

    def test_the_fence_is_called_with_the_bound_pk_and_epoch(self, patched):
        _, repository = patched
        record = SqlTradeRecord(
            SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=_RecordingFactory()
        )

        record.persist(_recorded_trade())

        call = repository.stamp_activity_if_owner.call_args
        assert call.args[0] == SESSION_PK
        assert call.kwargs["owner_epoch"] == OWNER_EPOCH


class TestRowMapping:
    """Every column equals the RecordedTrade's (AC #2's list)."""

    def test_every_field_maps_and_the_row_is_session_owned(self, patched):
        _, repository = patched
        record = SqlTradeRecord(
            SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=_RecordingFactory()
        )
        recorded = _recorded_trade()

        record.persist(recorded)

        row = repository.insert_trade_if_absent.call_args.args[0]
        assert row.session_id == SESSION_PK
        assert row.backtest_run_id is None
        assert row.instrument_id == recorded.trade.instrument_id
        assert row.trade_id == recorded.trade.trade_id
        assert row.venue_order_id == recorded.trade.venue_order_id
        assert row.client_order_id == recorded.trade.client_order_id
        assert row.order_side == recorded.trade.order_side
        assert row.quantity == recorded.trade.quantity
        assert row.entry_price == recorded.trade.entry_price
        assert row.exit_price == recorded.trade.exit_price
        assert row.commission_amount == recorded.trade.commission_amount
        assert row.commission_currency == recorded.trade.commission_currency
        assert row.fees_amount == Decimal("0.00")
        assert row.entry_timestamp == recorded.trade.entry_timestamp
        assert row.exit_timestamp == recorded.trade.exit_timestamp
        assert row.profit_loss == recorded.profit_loss
        assert row.profit_pct == recorded.profit_pct
        assert row.holding_period_seconds == recorded.holding_period_seconds

    def test_a_none_commission_passes_through_as_none_not_zero(self, patched):
        """3.5's flip resolution: unknown, never free."""
        _, repository = patched
        record = SqlTradeRecord(
            SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=_RecordingFactory()
        )
        recorded = _recorded_trade(
            trade=_recorded_trade().trade.model_copy(
                update={"commission_amount": None, "commission_currency": None}
            )
        )

        record.persist(recorded)

        row = repository.insert_trade_if_absent.call_args.args[0]
        assert row.commission_amount is None
        assert row.commission_currency is None

    def test_the_return_value_is_the_inserts_bool(self, patched):
        _, repository = patched
        repository.insert_trade_if_absent.return_value = False
        record = SqlTradeRecord(
            SESSION_PK, owner_epoch=OWNER_EPOCH, session_factory=_RecordingFactory()
        )

        assert record.persist(_recorded_trade()) is False


class TestTheAdapterModuleImportsNoNautilusTrader:
    def test_no_nautilus_trader_name_reaches_the_module(self):
        import ast
        import inspect

        from src.services import trade_record as adapter_module

        tree = ast.parse(inspect.getsource(adapter_module))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)

        assert not any("nautilus_trader" in name for name in names)

    def test_persists_only_parameter_is_annotated_recordedtrade(self):
        import inspect

        signature = inspect.signature(SqlTradeRecord.persist)
        params = [p for name, p in signature.parameters.items() if name != "self"]
        assert len(params) == 1
        assert params[0].annotation in ("RecordedTrade", RecordedTrade)
