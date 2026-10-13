"""The previous run's cash, read back from a real Redis (Story 4.7, D-B/D-C).

Integration tier under ``--forked``, on Story 4.6's scratch-namespace harness
(``test_live_session_view_redis.py``): a real ``Cache`` + ``ExecutionEngine``
writes an account with a *reported* ``AccountState`` and the IB exec client's
account-summary key through a real ``CacheDatabaseAdapter``, exactly as a
session's node leaves them when it stops. Two readers must get the same answer
from existing Redis state — no column, no migration (PO note):

1. **The pre-run snapshot (D-B).** A *fresh* cache restored at kernel init
   (``ExecutionEngine.load_cache()``, measured 1.3) holds the previous run's
   summary and account before the exec client's first push overwrites them, so
   ``capture_local_state`` names the cash and when IBKR last reported it.
2. **The session view (D-C).** ``read_session_view`` — the real default
   adapter — carries ``cash_recorded_at`` from ``load_account``. That the read
   stays load-only with ``load_account`` added is the ``MONITOR`` proof in
   ``test_live_session_view_redis.py``, which now reaches ``load_account``
   because its scratch session records cash.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import msgspec
import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.engine import ExecEngineConfig, ExecutionEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import AccountBalance, Money
from nautilus_trader.serialization.serializer import MsgSpecSerializer

from src.core.live_broker_state import ACCOUNT_SUMMARY_KEY_PREFIX
from src.core.live_cache import build_cache_config
from src.core.live_session_view import read_session_view
from src.core.live_startup_reconcile import capture_local_state
from src.models.broker_state import CashBalance
from tests.integration.core.test_live_session_view_redis import (
    ACCOUNT,
    IB_ACCOUNT,
    _redis_settings,
    _require_redis,  # noqa: F401  # module-scoped skip when Redis is down, re-used
    _Session,
)

pytestmark = pytest.mark.integration

RECORDED = datetime(2026, 9, 26, 20, 0, 1, 250000, tzinfo=UTC)
RECORDED_NANOS = 1_790_452_801_250_000_000


@pytest.fixture
def stopped_session():
    """A scratch session left the way a stopped node leaves it: an account whose
    last reported state is ``RECORDED`` and the summary IBKR last pushed."""
    scratch = _Session()
    account = scratch.cache.account(IB_ACCOUNT)
    account.apply(
        AccountState(
            account_id=IB_ACCOUNT,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            reported=True,
            balances=[AccountBalance(Money(100400, USD), Money(0, USD), Money(100400, USD))],
            margins=[],
            info={},
            event_id=UUID4(),
            ts_event=RECORDED_NANOS,
            ts_init=RECORDED_NANOS,
        )
    )
    scratch.cache.update_account(account)
    scratch.record_cash(ACCOUNT, 100000.52)
    scratch.flush_writes()
    yield scratch
    scratch.cleanup()


def _restored_cache(trader_id: str) -> tuple[Cache, CacheDatabaseAdapter]:
    """A fresh cache as ``NautilusKernel.__init__`` builds it for the next run."""
    adapter = CacheDatabaseAdapter(
        trader_id=TraderId(trader_id),
        instance_id=UUID4(),
        serializer=MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True),
        config=build_cache_config(_redis_settings()),
    )
    cache = Cache(database=adapter)
    clock = TestClock()
    ExecutionEngine(
        msgbus=MessageBus(trader_id=TraderId(trader_id), clock=clock),
        cache=cache,
        clock=clock,
        config=ExecEngineConfig(load_cache=True),
    ).load_cache()
    return cache, adapter


class TestThePreRunSnapshotReadsThePreviousRunsCash:
    def test_the_restored_cache_holds_the_summary_and_when_it_was_reported(self, stopped_session):
        cache, adapter = _restored_cache(stopped_session.trader_id)
        try:
            node = SimpleNamespace(
                cache=cache,
                kernel=SimpleNamespace(
                    exec_engine=SimpleNamespace(
                        _clients={"INTERACTIVE_BROKERS": SimpleNamespace(account_id=IB_ACCOUNT)}
                    )
                ),
            )
            raw = cache.get(f"{ACCOUNT_SUMMARY_KEY_PREFIX}{ACCOUNT}")

            snapshot = capture_local_state(node, _NullLog())
        finally:
            adapter.close()

        assert json.loads(raw)["USD"]["TotalCashValue"] == 100000.52
        assert snapshot is not None
        assert snapshot.cash == (CashBalance("USD", Decimal("100000.52")),)
        assert snapshot.cash_recorded_at == RECORDED


class TestTheSessionViewSaysWhenItsCashWasRecorded:
    def test_read_session_view_carries_the_accounts_last_reported_time(self, stopped_session):
        view = read_session_view(stopped_session.session_id, ACCOUNT, _redis_settings())

        assert view.cash == (CashBalance("USD", Decimal("100000.52")),)
        assert view.cash_recorded_at == RECORDED


class _NullLog:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None
