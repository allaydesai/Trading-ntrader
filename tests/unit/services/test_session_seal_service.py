"""Unit tests for the seal (Story 5.3) — no database, no ``Portfolio``.

The async session is a stand-in whose ``run_sync(fn, *args)`` calls
``fn(sync, *args)``, exactly the shape ``AsyncSession.run_sync`` has. The
trading-session repository is a ``MagicMock`` (spec'd on the sync twin) and
``SessionService`` runs **for real** over it, so the state machine these tests
exercise is the production one. The results computation (Story 5.2's
``reconstruct_session_results``) and the backtest write are spies at their
module targets: the computation is proven over a real ``Portfolio`` in 5.2's
component tier, and the write against Postgres in
``tests/integration/db/test_session_seal.py``.
"""

import ast
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from nautilus_trader.model.objects import MONEY_MAX
from structlog.testing import capture_logs

from src.core.live_cache import RedisUnreachableError
from src.core.live_session_view import SessionViewFailure, SessionViewUnavailableError
from src.core.session_equity import SessionResults
from src.core.strategy_registry import StrategyRegistry
from src.db.exceptions import InvalidSessionTransition, SealRefusedError
from src.db.models.backtest import BacktestRun
from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.backtest_result import BacktestResult
from src.models.reconciliation import (
    OpenLeg,
    SessionHoldings,
    SessionView,
    ViewPosition,
    WorkingOrder,
)
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services import session_seal_service as seal_module
from src.services.session_seal_service import (
    INSTRUMENT_SYMBOL_MAX,
    LINKED_BACKTEST_WARNING_EVENT,
    NONE_OPEN_DISPOSITION,
    NONE_WORKING_DISPOSITION,
    OTHER_ACCOUNT_DISPOSITION,
    PAPER_DATA_SOURCE,
    PAPER_RUN_TYPE,
    RECORDED_OPEN_DISPOSITION,
    UNKNOWN_HOLDINGS_DISPOSITION,
    UNKNOWN_ORDERS_DISPOSITION,
    WORKING_ORDERS_DISPOSITION,
    open_trade,
    period_start,
    render_seal_report,
    require_sealable_capital,
    run_identity,
    seal_json_payload,
    seal_session,
    strategy_display_name,
)
from src.services.session_service import SessionService
from tests.unit.cli.commands.test_live_status_cli import _ar36_violations
from tests.unit.core.test_live_stop_path_is_inert import (
    FORBIDDEN_FLATTEN_FRAGMENT,
    FORBIDDEN_ORDER_METHODS,
)

pytestmark = pytest.mark.unit

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SEAL_MODULE = "src/services/session_seal_service.py"
SEAL_CLI_MODULE = "src/cli/commands/live_seal.py"

SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
SESSION_PK = 7
RUN_ID = UUID("44444444-4444-4444-4444-444444444444")
RUN_PK = 42
LINKED_RUN_ID = UUID("88888888-8888-8888-8888-888888888888")
CREATED = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
SEALED_AT = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
CAPITAL = Decimal("100000.00")
BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

#: The one series the reconstruction stub hands back — 5.2's shape, curve and all.
EQUITY_SERIES = {
    "granularity": "closed_trade",
    "basis": "per_trade_price_return",
    "closed_trades": 3,
    "excludes": ["open_positions", "reconciliation_owned_activity"],
    "curve": [
        {"time": 1, "value": 101000.0},
        {"time": 2, "value": 100500.0},
        {"time": 3, "value": 100700.0},
    ],
}


def _stored_spec(*bar_types: str, strategies: int = 1) -> dict[str, Any]:
    """A spec exactly as ``live create`` stores it (``SessionSpec.to_stored``)."""
    entries = [StrategySpec(strategy_id="sma_crossover", bar_types=bar_types or (BAR_TYPE,))]
    stored = SessionSpec(strategies=tuple(entries)).to_stored()
    if strategies > 1:
        stored["strategies"] = stored["strategies"] * strategies
    return stored


def _row(status: SessionStatus = SessionStatus.STOPPED, **overrides) -> TradingSession:
    row = TradingSession(
        session_id=SESSION_ID,
        name="paper-alpha",
        status=status,
        spec=_stored_spec(),
        owner_epoch=2,
        linked_backtest_run_id=None,
    )
    row.id = SESSION_PK
    row.created_at = CREATED
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


OPENED = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)


def _leg(instrument: str = "AAPL.NASDAQ", quantity: str = "-25", **overrides) -> OpenLeg:
    """One open position as the session's engine cache holds it (Story 5.5)."""
    signed = Decimal(quantity)
    owner = overrides.pop("strategy_id", "SMACrossover-000")
    fields: dict[str, Any] = {
        "position_id": f"{instrument}-{owner}",
        "instrument_id": instrument,
        "strategy_id": owner,
        "side": "BUY" if signed > 0 else "SELL",
        "quantity": signed,
        "avg_px_open": Decimal("187.25000000"),
        "opened_at": OPENED,
        "opening_order_id": f"O-20260902-143000-33333333-000-{owner}",
        "realized_pnl": Decimal("-1.00"),
        "commission_amount": Decimal("1.00"),
        "commission_currency": "USD",
    }
    fields.update(overrides)
    return OpenLeg(**fields)


def _holding(
    *legs: OpenLeg, skipped: int = 0, orders: tuple = (), unreadable: tuple = ()
) -> SessionHoldings:
    """Holdings whose view is the legs' own net — the reader's invariant."""
    net: dict[str, Decimal] = {}
    for leg in legs:
        net[leg.instrument_id] = net.get(leg.instrument_id, Decimal(0)) + leg.quantity
    positions = tuple(ViewPosition(i, q) for i, q in sorted(net.items()) if q != 0)
    view = SessionView("PAPER-33333333", positions, (), positions_skipped=skipped)
    return SessionHoldings(
        view=view, legs=legs, working_orders=orders, unreadable_orders=unreadable
    )


def _working(number: int = 1) -> WorkingOrder:
    return WorkingOrder(
        client_order_id=f"O-20261009-150000-33333333-000-{number}",
        instrument_id="AAPL.NASDAQ",
        side="SELL",
        quantity=Decimal("25"),
        order_type="LIMIT",
        status="ACCEPTED",
    )


def _result(**overrides) -> BacktestResult:
    values = dict(
        total_return=0.007,
        total_trades=3,
        winning_trades=2,
        losing_trades=1,
        final_balance=100700.0,
    )
    values.update(overrides)
    return BacktestResult(**values)


class _Session:
    """The ``AsyncSession`` surface the seal uses — and a record of the rest."""

    def __init__(self, events: list) -> None:
        self.sync = object()
        self.events = events
        self.flushed_run_types: list[str] = []
        self.run: Any = None

    async def run_sync(self, fn, *args):
        return fn(self.sync, *args)

    async def flush(self) -> None:
        self.events.append("flush")
        if self.run is not None:
            self.flushed_run_types.append(self.run.run_type)

    async def commit(self) -> None:  # pragma: no cover - asserted never called
        self.events.append("commit")

    async def rollback(self) -> None:  # pragma: no cover - asserted never called
        self.events.append("rollback")


@dataclass
class Harness:
    """Every seam of one seal, wired and recording."""

    monkeypatch: pytest.MonkeyPatch
    row: TradingSession = field(default_factory=_row)
    holdings: SessionHoldings = field(default_factory=_holding)
    trades: list = field(default_factory=lambda: ["trade-1", "trade-2", "trade-3"])
    #: Story 5.5: the open-trade rows a forced seal wrote, in order.
    inserted: list = field(default_factory=list)
    insert_result: bool = True
    backfilled: int | None = None
    linked: Any = None
    result: BacktestResult = field(default_factory=_result)
    reconstruct_error: Exception | None = None
    events: list = field(default_factory=list)
    #: "lock" (a ``FOR UPDATE`` read) and "clock" (a read of the seal's
    #: clock), in the order they happened — the seal instant must follow the lock.
    order: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.db = _Session(self.events)
        self.repository = MagicMock(spec=SyncTradingSessionRepository)
        self.repository.session = MagicMock()
        self.repository.find_by_session_id.side_effect = self._find
        self.repository.find_by_name.side_effect = lambda *a, **k: self.row
        self.repository.find_session_trades.side_effect = self._trades
        self.repository.backfill_trades_run_id.side_effect = self._backfill
        self.repository.insert_trade_if_absent.side_effect = self._insert
        self.monkeypatch.setattr(
            seal_module, "SyncTradingSessionRepository", lambda sync: self.repository
        )

        self.backtest_repository = MagicMock()
        self.backtest_repository.find_by_run_id = AsyncMock(side_effect=lambda _: self.linked)
        self.monkeypatch.setattr(
            seal_module, "BacktestRepository", lambda db: self.backtest_repository
        )

        self.save = AsyncMock(side_effect=self._save)
        self.monkeypatch.setattr(
            seal_module,
            "BacktestPersistenceService",
            lambda repository: SimpleNamespace(save_backtest_results=self.save),
        )
        self.reconstruct = MagicMock(side_effect=self._reconstruct)
        self.monkeypatch.setattr(seal_module, "reconstruct_session_results", self.reconstruct)
        self.reader = MagicMock(side_effect=self._read)

    def _find(self, session_id: UUID, *, for_update: bool = False) -> TradingSession:
        if for_update:
            self.order.append("lock")
        return self.row

    def clock(self, *stamps: datetime):
        """A seal clock that records when it is read, returning ``stamps`` in turn."""
        remaining = iter(stamps or (SEALED_AT,) * 3)

        def read() -> datetime:
            self.order.append("clock")
            return next(remaining)

        return read

    def _read(self, session_id: UUID) -> SessionHoldings:
        self.events.append("read_view")
        return self.holdings

    def _insert(self, trade: Trade) -> bool:
        self.events.append("insert_open")
        self.inserted.append(trade)
        return self.insert_result

    def _trades(self, session_pk: int) -> list:
        """The rows a real read would return: the closed ones, then any open rows
        this seal wrote in the same transaction (DB id order)."""
        self.events.append("find_trades")
        return [*self.trades, *self.inserted]

    def _backfill(self, session_pk: int, run_pk: int) -> int:
        self.events.append("backfill")
        linked = len(self.trades) + len(self.inserted)
        return linked if self.backfilled is None else self.backfilled

    def _reconstruct(self, trades, *, starting_capital, start, end) -> SessionResults:
        self.events.append("reconstruct")
        if self.reconstruct_error is not None:
            raise self.reconstruct_error
        return SessionResults(self.result, {"equity_series": dict(EQUITY_SERIES)})

    async def _save(self, **kwargs):
        self.events.append("save")
        self.db.run = SimpleNamespace(
            id=RUN_PK,
            run_id=kwargs["run_id"],
            run_type="backtest",
            config_snapshot=kwargs["config_snapshot"],
            # What `create_backtest_run`'s refresh hands back: `Numeric(20,2)`.
            initial_capital=kwargs["initial_capital"].quantize(Decimal("0.01")),
        )
        return self.db.run

    async def seal(self, *, clock=None, capital: Decimal = CAPITAL, force: bool = False):
        return await seal_session(
            self.db,
            "paper-alpha",
            starting_capital=capital,
            read_open_positions=self.reader,
            force=force,
            clock=clock or self.clock(),
            new_run_id=lambda: RUN_ID,
        )

    @property
    def save_kwargs(self) -> dict[str, Any]:
        return self.save.call_args.kwargs


@pytest.fixture
def harness(monkeypatch) -> Harness:
    return Harness(monkeypatch)


class TestASealOfAStoppedSession:
    @pytest.mark.acceptance_criterion(
        "5.3a",
        "seal on a stopped session writes the run + metrics, backfills trades, sets "
        "sealed_run_id/sealed_at and transitions to sealed",
        SOURCE,
    )
    async def test_every_step_runs_in_order_and_the_row_ends_sealed(self, harness):
        report = await harness.seal()

        assert harness.events == [
            "read_view",
            "find_trades",
            "reconstruct",
            "save",
            "flush",
            "backfill",
        ]
        assert harness.row.status is SessionStatus.SEALED
        assert harness.row.sealed_at == SEALED_AT
        assert harness.row.sealed_run_id == RUN_ID
        harness.repository.find_session_trades.assert_called_once_with(SESSION_PK)
        harness.repository.backfill_trades_run_id.assert_called_once_with(SESSION_PK, RUN_PK)
        assert report.run_id == RUN_ID
        assert report.trades_backfilled == 3
        assert report.closed_trade_count == 3

    async def test_the_session_moves_through_transition_once_then_is_linked(
        self, harness, monkeypatch
    ):
        """The PO's AR37 note: status changes through ``transition()`` only."""
        calls: list = []
        real_transition = SessionService.transition
        real_link = SessionService.record_sealed_run

        def transition(self, session_id, *, to, owner_epoch=None):
            calls.append(("transition", session_id, to))
            return real_transition(self, session_id, to=to, owner_epoch=owner_epoch)

        def link(self, session_id, *, sealed_run_id):
            calls.append(("link", session_id, sealed_run_id))
            return real_link(self, session_id, sealed_run_id=sealed_run_id)

        monkeypatch.setattr(SessionService, "transition", transition)
        monkeypatch.setattr(SessionService, "record_sealed_run", link)

        await harness.seal()

        assert calls == [
            ("transition", SESSION_ID, SessionStatus.SEALED),
            ("link", SESSION_ID, RUN_ID),
        ]

    async def test_the_trades_read_are_the_trades_reconstructed(self, harness):
        await harness.seal()

        (trades,) = harness.reconstruct.call_args.args
        assert trades == ["trade-1", "trade-2", "trade-3"]

    async def test_the_seal_never_ends_the_transaction_itself(self, harness):
        """The caller owns commit and rollback (``SessionService``'s rule)."""
        await harness.seal()

        assert "commit" not in harness.events
        assert "rollback" not in harness.events


class TestRefusedBeforeAnythingElse:
    @pytest.mark.acceptance_criterion("5.3b", "seal of a running session is refused", SOURCE)
    async def test_a_running_session_is_refused_before_any_other_step(self, harness):
        harness.row = _row(SessionStatus.RUNNING)

        with pytest.raises(InvalidSessionTransition, match="running"):
            await harness.seal()

        assert harness.events == []
        harness.reader.assert_not_called()
        harness.save.assert_not_called()
        assert harness.row.status is SessionStatus.RUNNING
        assert harness.row.sealed_run_id is None
        assert harness.row.sealed_at is None

    @pytest.mark.acceptance_criterion(
        "5.3c", "seal of an already-sealed session is refused (sealed is terminal)", SOURCE
    )
    async def test_an_already_sealed_session_is_refused_and_keeps_its_first_seal(self, harness):
        first = UUID("99999999-9999-9999-9999-999999999999")
        harness.row = _row(SessionStatus.SEALED, sealed_run_id=first, sealed_at=CREATED)

        with pytest.raises(InvalidSessionTransition, match="sealed"):
            await harness.seal()

        assert harness.events == []
        assert harness.row.sealed_run_id == first
        assert harness.row.sealed_at == CREATED

    async def test_a_created_session_is_refused_too(self, harness):
        harness.row = _row(SessionStatus.CREATED)

        with pytest.raises(InvalidSessionTransition):
            await harness.seal()

        assert harness.events == []

    @pytest.mark.parametrize("capital", ["-1", "0", "100000.001", "1E+15", "-0"])
    async def test_a_capital_the_record_cannot_carry_is_refused_before_the_lock(
        self, harness, capital
    ):
        """PO ruling R1a and the review's constraint: judged once, up front —
        5.1's positive/whole-cent rule and Nautilus's largest ``Money`` — before
        the row is locked, the clock read or the engine cache touched."""
        with pytest.raises(SealRefusedError, match="starting capital"):
            await harness.seal(capital=Decimal(capital))

        assert harness.order == []
        assert harness.events == []
        harness.reader.assert_not_called()

    def test_the_largest_money_nautilus_holds_is_accepted_and_a_cent_more_is_not(self):
        largest = Decimal(str(MONEY_MAX)).quantize(Decimal("0.01"))

        require_sealable_capital(largest)
        with pytest.raises(SealRefusedError, match="starting capital"):
            require_sealable_capital(largest + Decimal("0.01"))

    def test_a_capital_that_is_not_a_decimal_is_refused(self):
        with pytest.raises(SealRefusedError, match="starting capital"):
            require_sealable_capital(100000.0)  # type: ignore[arg-type]

    async def test_the_seal_instant_is_read_after_the_row_is_locked(self, harness):
        """Code review: read before the lock, a stop that held the row and
        committed later would leave ``sealed_at`` before ``last_stopped_at``."""
        await harness.seal()

        assert "clock" in harness.order
        assert harness.order.index("lock") < harness.order.index("clock")


class TestOpenPositionsFailClosed:
    """PO ruling R2a: read-only on the engine cache, fail closed, never the broker."""

    async def test_a_held_position_refuses_naming_instrument_and_quantity(self, harness):
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "-25"))

        with pytest.raises(SealRefusedError) as raised:
            await harness.seal()

        assert "AAPL.NASDAQ" in str(raised.value)
        assert "-25" in str(raised.value)
        assert "find_trades" not in harness.events
        harness.save.assert_not_called()

    async def test_the_refusal_names_only_the_remedy_that_works(self, harness):
        """Code review: flattening at the broker leaves the engine cache as it
        was (the seal still refuses) and makes the next start refuse — so the
        text names the one path that clears it, the strategy's own exit."""
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"))

        with pytest.raises(SealRefusedError) as raised:
            await harness.seal()

        message = str(raised.value)
        assert "restart the session" in message
        assert "broker" not in message
        assert "flatten" not in message

    async def test_positions_on_another_account_refuse(self, harness):
        harness.holdings = _holding(skipped=2)

        with pytest.raises(SealRefusedError, match="another account") as raised:
            await harness.seal()

        # Code review P7: a changed TWS_ACCOUNT is the likely cause — check it
        # before reaching for --force.
        message = str(raised.value)
        assert "Check TWS_ACCOUNT" in message
        assert message.index("Check TWS_ACCOUNT") < message.index("--force")
        harness.save.assert_not_called()

    async def test_an_unreadable_view_refuses_and_is_never_read_as_flat(self, harness):
        harness.reader.side_effect = SessionViewUnavailableError(
            SessionViewFailure.NO_ENGINE_STATE, "no keys under the namespace"
        )

        with pytest.raises(SessionViewUnavailableError):
            await harness.seal()

        harness.save.assert_not_called()

    async def test_an_empty_view_seals_and_says_so(self, harness):
        report = await harness.seal()

        harness.reader.assert_called_once_with(SESSION_ID)
        assert report.open_positions == ()
        assert report.open_positions_disposition == NONE_OPEN_DISPOSITION


class TestOpenPositionsAreTheOperatorsDecision:
    """Story 5.5 (FR47, AR30, PO rulings R1a/R2a): refuse by default, record with ``--force``."""

    @pytest.mark.acceptance_criterion(
        "5.5a",
        "seal without --force refuses a stopped session with open positions, listing each "
        "open position with its instrument and quantity",
        SOURCE,
    )
    async def test_without_force_every_open_position_is_listed_and_nothing_is_written(
        self, harness
    ):
        harness.holdings = _holding(
            _leg("AAPL.NASDAQ", "10"),
            _leg("AAPL.NASDAQ", "5", strategy_id="EXTERNAL"),
            _leg("NVDA.NASDAQ", "-3"),
        )

        with pytest.raises(SealRefusedError) as raised:
            await harness.seal()

        # Code review P4: each open position — R1a's Nautilus position — with its
        # instrument, quantity and owner, not the instrument's net.
        message = str(raised.value)
        assert "AAPL.NASDAQ 10 (SMACrossover-000)" in message
        assert "AAPL.NASDAQ 5 (EXTERNAL)" in message
        assert "NVDA.NASDAQ -3 (SMACrossover-000)" in message
        assert "AAPL.NASDAQ 15" not in message
        assert "--force" in message
        assert harness.events == ["read_view"]
        assert harness.inserted == []
        harness.save.assert_not_called()

    async def test_the_refusal_names_force_without_any_lifecycle_synonym(self, harness):
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"))

        with pytest.raises(SealRefusedError) as raised:
            await harness.seal()

        assert _ar36_violations(str(raised.value)) == []

    @pytest.mark.acceptance_criterion(
        "5.5b",
        "seal --force seals and persists each open position as an open trade with a null exit, "
        "the shape backtests use for positions open at the end of data",
        SOURCE,
    )
    async def test_with_force_each_open_position_is_one_open_trade_with_no_exit(self, harness):
        legs = (
            _leg("AAPL.NASDAQ", "10"),
            _leg("AAPL.NASDAQ", "5", strategy_id="EXTERNAL"),
            _leg("NVDA.NASDAQ", "-3"),
        )
        harness.holdings = _holding(*legs)

        report = await harness.seal(force=True)

        assert harness.row.status is SessionStatus.SEALED
        assert [row.trade_id for row in harness.inserted] == [leg.position_id for leg in legs]
        for row, leg in zip(harness.inserted, legs):
            assert isinstance(row, Trade)
            assert row.exit_price is None
            assert row.exit_timestamp is None
            assert row.session_id == SESSION_PK
            assert row.backtest_run_id is None  # linked by the backfill, with the rest
            assert row.instrument_id == leg.instrument_id
            assert row.quantity == abs(leg.quantity)
        assert report.open_trades_recorded == 3
        assert report.forced is True

    async def test_the_open_rows_are_written_before_the_trades_are_read(self, harness):
        """D-G: so the reconstruction sees them (and leaves them out, 5.2) and the
        backfill links them — its count check covers them too."""
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"))

        report = await harness.seal(force=True)

        assert harness.events == [
            "read_view",
            "insert_open",
            "find_trades",
            "reconstruct",
            "save",
            "flush",
            "backfill",
        ]
        (trades,) = harness.reconstruct.call_args.args
        assert trades == [*harness.trades, *harness.inserted]
        assert report.trades_backfilled == 4
        assert report.closed_trade_count == EQUITY_SERIES["closed_trades"]

    async def test_the_open_row_is_the_backtests_open_shape(self, harness):
        """F7 and D-B: the backtest's mapping, field by field. The parity against a
        real backtest row is pinned in the component tier."""
        leg = _leg("NVDA.NASDAQ", "-3")
        harness.holdings = _holding(leg)

        await harness.seal(force=True)

        [row] = harness.inserted
        assert {
            "trade_id": row.trade_id,
            "venue_order_id": row.venue_order_id,
            "client_order_id": row.client_order_id,
            "order_side": row.order_side,
            "quantity": row.quantity,
            "entry_price": row.entry_price,
            "exit_price": row.exit_price,
            "commission_amount": row.commission_amount,
            "commission_currency": row.commission_currency,
            "fees_amount": row.fees_amount,
            "profit_loss": row.profit_loss,
            "profit_pct": row.profit_pct,
            "holding_period_seconds": row.holding_period_seconds,
            "entry_timestamp": row.entry_timestamp,
            "exit_timestamp": row.exit_timestamp,
        } == {
            "trade_id": "NVDA.NASDAQ-SMACrossover-000",
            "venue_order_id": leg.opening_order_id,
            "client_order_id": None,
            "order_side": "SELL",
            "quantity": Decimal("3"),
            "entry_price": Decimal("187.25000000"),
            "exit_price": None,
            "commission_amount": Decimal("1.00"),
            "commission_currency": "USD",
            "fees_amount": Decimal("0.00"),
            "profit_loss": Decimal("-1.00"),
            "profit_pct": None,
            "holding_period_seconds": None,
            "entry_timestamp": OPENED,
            "exit_timestamp": None,
        }

    async def test_an_open_row_that_is_not_written_refuses(self, harness):
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"))
        harness.insert_result = False

        with pytest.raises(SealRefusedError, match="AAPL.NASDAQ-SMACrossover-000"):
            await harness.seal(force=True)

        harness.save.assert_not_called()

    def test_an_open_row_the_trade_model_refuses_is_a_seal_refusal_by_position(self):
        """``trades.quantity`` holds 8 decimal places; a finer one is refused by
        name, never rounded or left to Postgres."""
        leg = _leg("BTCUSDT.BINANCE", "0.000000001")

        with pytest.raises(SealRefusedError, match="BTCUSDT.BINANCE-SMACrossover-000"):
            open_trade(leg, SESSION_PK)

    @pytest.mark.parametrize(
        "field, column", [("position_id", "trade_id"), ("opening_order_id", "venue_order_id")]
    )
    def test_an_id_longer_than_its_column_is_refused_by_name(self, field, column):
        """Code review P8: ``TradeCreate`` sets no length on these, and Postgres
        would answer ``StringDataRightTruncation`` naming nothing."""
        length = Trade.__table__.c[column].type.length
        leg = _leg("AAPL.NASDAQ", "10", **{field: "X" * (length + 1)})

        with pytest.raises(SealRefusedError, match=rf"{column}.*{length}"):
            open_trade(leg, SESSION_PK)

    def test_an_id_exactly_its_columns_length_is_written(self):
        length = Trade.__table__.c["trade_id"].type.length

        row = open_trade(_leg("AAPL.NASDAQ", "10", position_id="X" * length), SESSION_PK)

        assert len(row.trade_id) == length

    @pytest.mark.acceptance_criterion(
        "5.5c",
        "a --force seal's report states the disposition explicitly: the positions remain at "
        "the broker and are no longer owned by any session",
        SOURCE,
    )
    async def test_a_forced_seal_states_the_disposition_in_both_forms(self, harness):
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"), _leg("NVDA.NASDAQ", "-3"))

        report = await harness.seal(force=True)

        payload = seal_json_payload(report)
        human = render_seal_report(report)
        assert report.open_positions_disposition == RECORDED_OPEN_DISPOSITION
        assert "remain at the broker" in RECORDED_OPEN_DISPOSITION
        assert "no longer owned by any session" in RECORDED_OPEN_DISPOSITION
        assert payload["open_positions_disposition"] == RECORDED_OPEN_DISPOSITION
        assert f"open_positions_disposition: {RECORDED_OPEN_DISPOSITION}" in human
        assert payload["open_positions"] == [
            {"instrument_id": "AAPL.NASDAQ", "quantity": "10"},
            {"instrument_id": "NVDA.NASDAQ", "quantity": "-3"},
        ]
        assert payload["forced"] is True
        assert payload["open_trades_recorded"] == 2

    async def test_the_report_says_open_trades_and_realized_pnl_are_outside_the_metrics(
        self, harness
    ):
        """D-E and code review P3: the 5.1/5.2 open-leg debt, surfaced in the
        report the operator reads — including P&L already realized on a partly
        reduced position, which lives only on its open row."""
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"))

        human = "\n".join(render_seal_report(await harness.seal(force=True)))

        assert "outside the record's metrics" in human
        assert "P&L already realized on them" in human

    ALL_DISPOSITIONS = (
        NONE_OPEN_DISPOSITION,
        RECORDED_OPEN_DISPOSITION,
        OTHER_ACCOUNT_DISPOSITION,
        UNKNOWN_HOLDINGS_DISPOSITION,
        NONE_WORKING_DISPOSITION,
        WORKING_ORDERS_DISPOSITION,
        UNKNOWN_ORDERS_DISPOSITION,
    )

    @pytest.mark.parametrize("text", ALL_DISPOSITIONS)
    def test_no_disposition_uses_a_lifecycle_synonym(self, text):
        assert _ar36_violations(text) == []

    @pytest.mark.parametrize("text", ALL_DISPOSITIONS)
    def test_every_disposition_names_the_engine_cache_as_its_basis(self, text):
        """Code review P2: the seal reads a cache, not the broker — no disposition
        states broker state as fact without saying where it came from."""
        assert "the session's engine cache" in text

    def test_working_orders_say_what_the_cache_cannot_see(self):
        """R3b's stated limitation, in the report itself (code review P2)."""
        assert "cannot see" in WORKING_ORDERS_DISPOSITION
        assert "while the session was stopped" in WORKING_ORDERS_DISPOSITION

    def test_unknown_holdings_tell_the_operator_to_check_the_redis_it_read(self):
        """Code review DN2a: a reachable but wrong Redis reads exactly like a
        flushed one — the report names the settings to confirm."""
        assert "REDIS_HOST" in UNKNOWN_HOLDINGS_DISPOSITION
        assert "REDIS_PORT" in UNKNOWN_HOLDINGS_DISPOSITION

    @pytest.mark.acceptance_criterion(
        "5.5e", "a stopped session with no open positions seals without requiring --force", SOURCE
    )
    async def test_no_open_positions_seal_without_force(self, harness):
        report = await harness.seal()

        assert harness.row.status is SessionStatus.SEALED
        assert harness.inserted == []
        assert report.forced is False
        assert report.open_trades_recorded == 0
        assert report.open_positions_disposition == NONE_OPEN_DISPOSITION

    async def test_force_with_nothing_held_seals_exactly_as_without(self, monkeypatch):
        """D-F: only ``forced`` differs — the operator's choice, reported honestly.
        Each harness patches the module's seams, so each is built just before its seal."""
        plain = seal_json_payload(await Harness(monkeypatch).seal())
        forced_harness = Harness(monkeypatch)
        forced = seal_json_payload(await forced_harness.seal(force=True))

        assert forced.pop("forced") is True
        assert plain.pop("forced") is False
        assert forced == plain
        assert forced_harness.inserted == []


class TestWhatForceOverrides:
    """PO ruling R2a (DN2a): what ``--force`` may seal past, and what it may not."""

    async def test_positions_on_another_account_are_counted_not_recorded(self, harness):
        harness.holdings = _holding(skipped=2)

        report = await harness.seal(force=True)

        assert report.positions_on_another_account == 2
        assert harness.inserted == []
        assert "another account" in report.open_positions_disposition
        assert "no longer owned by any session" in report.open_positions_disposition
        assert seal_json_payload(report)["positions_on_another_account"] == 2

    async def test_held_and_other_account_positions_are_both_disposed_of(self, harness):
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"), skipped=1)

        report = await harness.seal(force=True)

        assert RECORDED_OPEN_DISPOSITION in report.open_positions_disposition
        assert "1 position(s) held for another account" in report.open_positions_disposition

    async def test_no_engine_state_seals_with_unknown_holdings_never_none_open(self, harness):
        """DN2a's way out: nothing to read is *unknown*, never "none open"."""
        harness.reader.side_effect = SessionViewUnavailableError(
            SessionViewFailure.NO_ENGINE_STATE, "no keys under the namespace"
        )

        report = await harness.seal(force=True)

        assert harness.row.status is SessionStatus.SEALED
        assert harness.inserted == []
        assert report.open_positions == ()
        assert report.open_positions_disposition == UNKNOWN_HOLDINGS_DISPOSITION
        assert report.working_orders_disposition == UNKNOWN_ORDERS_DISPOSITION
        assert report.open_positions_disposition != NONE_OPEN_DISPOSITION

    @pytest.mark.parametrize(
        "failure",
        [
            SessionViewUnavailableError(SessionViewFailure.UNREADABLE, "a corrupt fill list"),
            SessionViewUnavailableError(SessionViewFailure.ADAPTER_INCOMPATIBLE, "moved"),
            RedisUnreachableError("Redis is not reachable"),
            SealRefusedError("TWS_ACCOUNT is not set"),
        ],
        ids=["unreadable", "adapter_incompatible", "redis_unreachable", "no_account"],
    )
    async def test_a_fixable_read_failure_still_refuses_under_force(self, harness, failure):
        """A retry reads holdings a forced seal would otherwise drop unrecorded."""
        harness.reader.side_effect = failure

        with pytest.raises(type(failure)):
            await harness.seal(force=True)

        harness.save.assert_not_called()
        assert harness.inserted == []


class TestWorkingOrdersAreReported:
    """PO ruling R3b (DN1a): named with their disposition — never a refusal, never a cancel."""

    async def test_a_working_order_is_reported_and_the_flat_session_still_seals(self, harness):
        harness.holdings = _holding(orders=(_working(1), _working(2)))

        report = await harness.seal()

        assert harness.row.status is SessionStatus.SEALED
        assert report.working_orders == (_working(1), _working(2))
        assert report.working_orders_disposition == WORKING_ORDERS_DISPOSITION
        assert seal_json_payload(report)["working_orders"] == [
            {
                "client_order_id": _working(1).client_order_id,
                "instrument_id": "AAPL.NASDAQ",
                "side": "SELL",
                "quantity": "25",
                "order_type": "LIMIT",
                "status": "ACCEPTED",
            },
            {
                "client_order_id": _working(2).client_order_id,
                "instrument_id": "AAPL.NASDAQ",
                "side": "SELL",
                "quantity": "25",
                "order_type": "LIMIT",
                "status": "ACCEPTED",
            },
        ]

    async def test_the_report_says_they_stay_working_and_the_seal_cancels_nothing(self, harness):
        """Code review P10: read from the rendered report, not from the constant."""
        harness.holdings = _holding(orders=(_working(1),))

        human = "\n".join(render_seal_report(await harness.seal()))

        assert "owned by no session" in human
        assert "the seal cancels nothing" in human
        assert _working(1).client_order_id in human

    async def test_no_working_order_reads_none_working(self, harness):
        report = await harness.seal()

        assert report.working_orders == ()
        assert report.unreadable_orders == ()
        assert report.working_orders_disposition == NONE_WORKING_DISPOSITION

    async def test_an_unreadable_order_is_named_and_the_flat_session_still_seals(self, harness):
        """Code review DN1a: never a refusal, with or without --force — and named,
        never silently left out."""
        harness.holdings = _holding(orders=(_working(1),), unreadable=("O-broken-9",))

        report = await harness.seal()

        assert harness.row.status is SessionStatus.SEALED
        assert report.unreadable_orders == ("O-broken-9",)
        assert "1 order(s) could not be read" in report.working_orders_disposition
        assert WORKING_ORDERS_DISPOSITION in report.working_orders_disposition
        payload = seal_json_payload(report)
        assert payload["unreadable_orders"] == ["O-broken-9"]
        assert "O-broken-9" in "\n".join(render_seal_report(report))

    async def test_only_unreadable_orders_still_name_the_engine_cache_and_tws(self, harness):
        harness.holdings = _holding(unreadable=("O-broken-9",))

        report = await harness.seal(force=True)

        disposition = report.working_orders_disposition
        assert NONE_WORKING_DISPOSITION not in disposition
        assert "the session's engine cache" in disposition
        assert "check them in TWS" in disposition


class TestTheHoldingsAreKnownOrSaidToBeUnknown:
    """Code review P1: an unknown holding never reads as a concrete "none"."""

    async def test_a_read_holding_is_known(self, harness):
        report = await harness.seal()

        assert report.holdings_known is True
        assert seal_json_payload(report)["holdings_known"] is True

    async def test_no_engine_state_under_force_is_unknown_in_every_form(self, harness):
        harness.reader.side_effect = SessionViewUnavailableError(
            SessionViewFailure.NO_ENGINE_STATE, "no keys under the namespace"
        )

        report = await harness.seal(force=True)

        payload = seal_json_payload(report)
        assert report.holdings_known is False
        assert payload["holdings_known"] is False
        assert "holdings_known: False" in render_seal_report(report)


class TestTheWriteIsTheBacktestPathsOwn:
    @pytest.mark.acceptance_criterion(
        "5.3e",
        "the seal reuses BacktestPersistenceService NaN/Infinity validation and "
        "DuplicateRecordError handling verbatim",
        SOURCE,
    )
    async def test_the_run_is_written_through_save_backtest_results(self, harness):
        await harness.seal()

        harness.save.assert_awaited_once()
        kwargs = harness.save_kwargs
        assert kwargs["run_id"] == RUN_ID
        assert kwargs["backtest_result"] is harness.result
        assert kwargs["initial_capital"] is CAPITAL
        assert kwargs["data_source"] == PAPER_DATA_SOURCE
        assert kwargs.get("reproduced_from_run_id") is None

    @pytest.mark.acceptance_criterion(
        "5.3d", "the created run record's run_type is 'paper'", SOURCE
    )
    async def test_run_type_is_stamped_paper_after_the_write_and_flushed(self, harness):
        await harness.seal()

        assert harness.db.run.run_type == PAPER_RUN_TYPE == "paper"
        assert harness.db.flushed_run_types == ["paper"]
        assert harness.events.index("flush") > harness.events.index("save")

    async def test_the_period_is_created_at_to_the_seal_instant(self, harness):
        """R3a — and ``end_date`` is the very instant ``sealed_at`` was stamped
        with, however many times the clock would have ticked."""
        ticks = iter([SEALED_AT, SEALED_AT + timedelta(hours=1), SEALED_AT + timedelta(hours=2)])

        await harness.seal(clock=lambda: next(ticks))

        kwargs = harness.save_kwargs
        assert kwargs["start_date"] == CREATED
        assert kwargs["end_date"] == SEALED_AT == harness.row.sealed_at
        reconstructed = harness.reconstruct.call_args.kwargs
        assert reconstructed == {"starting_capital": CAPITAL, "start": CREATED, "end": SEALED_AT}

    async def test_the_snapshot_carries_the_series_beside_the_base_keys(self, harness):
        await harness.seal()

        snapshot = harness.save_kwargs["config_snapshot"]
        assert set(snapshot) == {
            "strategy_path",
            "config_path",
            "version",
            "config",
            "bar_type",
            "session_conditions",
        }
        assert snapshot["session_conditions"] == {"equity_series": EQUITY_SERIES}

    async def test_a_row_the_reconstruction_refuses_becomes_a_seal_refusal(self, harness):
        cause = ValueError("trade 17 has no exit_price; refusing to reconstruct a leg from it")
        harness.reconstruct_error = cause

        with pytest.raises(SealRefusedError, match="trade 17") as raised:
            await harness.seal()

        assert raised.value.__cause__ is cause
        harness.save.assert_not_called()

    async def test_a_backfill_that_misses_a_row_refuses(self, harness):
        harness.backfilled = 2

        with pytest.raises(SealRefusedError, match=r"read 3 trade row\(s\) but linked 2"):
            await harness.seal()

    async def test_an_arithmetic_failure_in_the_reconstruction_becomes_a_seal_refusal(
        self, harness
    ):
        """``decimal.InvalidOperation`` is an ``ArithmeticError``, not a
        ``ValueError``; it must not escape as an unclassified failure."""
        harness.reconstruct_error = InvalidOperation("quantize result has too many digits")

        with pytest.raises(SealRefusedError):
            await harness.seal()

        harness.save.assert_not_called()

    @pytest.mark.parametrize(
        "field_name, value, bound",
        [("cagr", 1.75e9, "Numeric(15,6)"), ("expectancy", -2e18, "Numeric(20,2)")],
        ids=["ratio", "money"],
    )
    async def test_a_metric_its_column_cannot_hold_is_refused_by_name(
        self, harness, field_name, value, bound
    ):
        """Code review: a 1-day session at +6% computes CAGR ≈ 1.75e9, past
        ``Numeric(15,6)``. Unrefused, Postgres answers with an unexplained
        ``DataError``; refused here, the operator reads which metric and why."""
        harness.result = _result(**{field_name: value})

        with pytest.raises(SealRefusedError) as raised:
            await harness.seal()

        assert field_name in str(raised.value)
        assert bound in str(raised.value)
        harness.save.assert_not_called()

    async def test_a_non_finite_metric_is_left_to_the_persistence_services_own_check(self, harness):
        """AR12: NaN/Infinity is the backtest path's validation to refuse,
        verbatim — the column-bound check must not shadow it."""
        harness.result = _result(sharpe_ratio=float("nan"))

        await harness.seal()

        harness.save.assert_awaited_once()

    async def test_the_report_carries_the_capital_as_stored_not_as_typed(self, harness):
        """PO constraint: stored as given (no normalisation), reported as stored."""
        typed = Decimal("1E+5")

        report = await harness.seal(capital=typed)

        assert harness.save_kwargs["initial_capital"] is typed
        assert seal_json_payload(report)["execution_conditions"]["initial_capital"] == "100000.00"


class TestLinkedBacktestIsReportedNeverAGate:
    """PO ruling R4a, refined: warn, still seal."""

    def _linked(self, status: str = "success", metrics: object | None = object()):
        return SimpleNamespace(run_id=LINKED_RUN_ID, execution_status=status, metrics=metrics)

    async def test_no_link_reads_nothing_and_warns_nothing(self, harness):
        report = await harness.seal()

        harness.backtest_repository.find_by_run_id.assert_not_called()
        assert report.linked_backtest_run_id is None
        assert report.linked_backtest_warning is None

    async def test_a_healthy_link_warns_nothing(self, harness):
        harness.row = _row(linked_backtest_run_id=LINKED_RUN_ID)
        harness.linked = self._linked()

        report = await harness.seal()

        assert report.linked_backtest_run_id == LINKED_RUN_ID
        assert report.linked_backtest_warning is None

    @pytest.mark.parametrize(
        "status, metrics, reason",
        [("failed", None, "failed"), ("success", None, "no metrics")],
        ids=["failed", "no_metrics"],
    )
    async def test_a_failed_or_metricless_link_warns_and_still_seals(
        self, harness, status, metrics, reason
    ):
        harness.row = _row(linked_backtest_run_id=LINKED_RUN_ID)
        harness.linked = self._linked(status, metrics)

        with capture_logs() as logs:
            report = await harness.seal()

        assert reason in report.linked_backtest_warning
        assert str(LINKED_RUN_ID) in report.linked_backtest_warning
        assert harness.row.status is SessionStatus.SEALED
        harness.save.assert_awaited_once()
        # Code review: logged by the CLI after the commit, never here — a seal
        # that later rolls back must leave no warning about a record that
        # does not exist (tests/unit/cli/commands/test_live_seal_cli.py).
        assert [log for log in logs if log["event"] == LINKED_BACKTEST_WARNING_EVENT] == []

    async def test_a_link_that_cannot_be_found_warns(self, harness):
        harness.row = _row(linked_backtest_run_id=LINKED_RUN_ID)
        harness.linked = None

        report = await harness.seal()

        assert "not found" in report.linked_backtest_warning


class TestRunIdentity:
    """D-E: the run's identity, read from the spec as stored."""

    def test_a_single_instrument_session_maps_like_a_cli_backtest(self):
        identity = run_identity(_stored_spec())

        definition = StrategyRegistry.get("sma_crossover")
        assert identity.strategy_type == "sma_crossover"
        assert identity.strategy_name == "Sma Crossover"
        assert identity.instrument_symbol == "AAPL"
        assert identity.config_snapshot == {
            "strategy_path": definition.strategy_path,
            "config_path": definition.config_path,
            "version": "1.0",
            "config": _stored_spec()["strategies"][0]["parameters"],
            "bar_type": "1-MINUTE-LAST",
        }

    def test_several_instruments_on_one_spec_join_their_symbols(self):
        identity = run_identity(
            _stored_spec(
                BAR_TYPE, "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL", "BRK.B.NYSE-1-MINUTE-LAST-EXTERNAL"
            )
        )

        assert identity.instrument_symbol == "AAPL,MSFT,BRK.B"
        assert identity.config_snapshot["bar_type"] == "1-MINUTE-LAST"

    def test_mixed_bar_specs_store_no_single_bar_type(self):
        identity = run_identity(_stored_spec(BAR_TYPE, "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"))

        assert identity.instrument_symbol == "AAPL"
        assert identity.config_snapshot["bar_type"] is None

    def test_two_strategies_are_refused(self):
        with pytest.raises(SealRefusedError, match="one strategy"):
            run_identity(_stored_spec(strategies=2))

    def test_no_strategies_are_refused(self):
        with pytest.raises(SealRefusedError, match="one strategy"):
            run_identity({"schema_version": 1, "strategies": []})

    def test_an_unregistered_strategy_is_refused_by_name(self):
        spec = _stored_spec()
        spec["strategies"][0]["strategy_id"] = "no_such_strategy"

        with pytest.raises(SealRefusedError, match="no_such_strategy"):
            run_identity(spec)

    def test_a_strategy_with_no_config_class_is_refused(self, monkeypatch):
        spec = _stored_spec()  # built through the real registry, before the patch
        definition = SimpleNamespace(strategy_path="m:S", config_path=None, name="sma_crossover")
        monkeypatch.setattr(StrategyRegistry, "get", classmethod(lambda cls, name: definition))

        with pytest.raises(SealRefusedError, match="config"):
            run_identity(spec)

    def test_a_symbol_list_longer_than_the_column_is_refused(self):
        bar_types = [f"SYM{i:02d}X.NASDAQ-1-MINUTE-LAST-EXTERNAL" for i in range(9)]

        with pytest.raises(SealRefusedError, match=str(INSTRUMENT_SYMBOL_MAX)):
            run_identity(_stored_spec(*bar_types))

    def test_the_symbol_cap_is_the_columns_own_length(self):
        assert INSTRUMENT_SYMBOL_MAX == BacktestRun.__table__.c.instrument_symbol.type.length

    def test_the_display_name_is_the_backtest_paths_for_every_registered_strategy(self):
        """F8: ``backtest_orchestrator._persist_results`` writes
        ``strategy_type.replace("_", " ").title()``. The orchestrator is
        untouched (AR44), so its one-liner cannot be imported — this pins the
        seal's copy to it for every name a session can carry."""
        names = StrategyRegistry.get_names()
        assert names, "the registry discovered nothing; this pin would be vacuous"
        for name in names:
            assert strategy_display_name(name) == name.replace("_", " ").title()


class TestPeriodStart:
    def test_the_period_starts_at_the_sessions_creation(self):
        """R3a: never after the first start, so CAGR is never overstated."""
        assert period_start(_row()) == CREATED

    def test_a_row_without_created_at_is_refused(self):
        row = _row()
        row.created_at = None

        with pytest.raises(SealRefusedError, match="created_at"):
            period_start(row)


class TestTheReport:
    PAYLOAD_KEYS = {
        "session_id",
        "name",
        "run_id",
        "run_type",
        "sealed_at",
        "closed_trade_count",
        "trades_backfilled",
        "forced",
        "holdings_known",
        "open_positions",
        "open_positions_disposition",
        "open_trades_recorded",
        "positions_on_another_account",
        "working_orders",
        "unreadable_orders",
        "working_orders_disposition",
        "linked_backtest_run_id",
        "linked_backtest_warning",
        "execution_conditions",
    }

    @pytest.mark.acceptance_criterion(
        "5.3g",
        "the seal report names run id, closed-trade count, open positions and disposition, "
        "and the execution-conditions summary, human and --json with identical fields",
        SOURCE,
    )
    async def test_human_and_json_carry_identical_fields(self, harness):
        """One report, both forms, every level: the human form's labels are the
        payload's keys at the top **and** inside every nested mapping."""
        report = await harness.seal()
        payload = seal_json_payload(report)
        lines = render_seal_report(report)

        top = {line.split(":", 1)[0] for line in lines if not line.startswith(" ")}
        every = {
            line.strip().split(":", 1)[0] for line in lines if not line.strip().startswith("- ")
        }
        assert set(payload) == self.PAYLOAD_KEYS
        assert top == set(payload)
        assert every == _keys_at_every_level(payload)
        assert "curve_points" in every  # the nested walk reached equity_series

    async def test_the_payload_is_json_native_with_money_as_strings(self, harness):
        payload = seal_json_payload(await harness.seal())

        assert json.loads(json.dumps(payload)) == payload
        assert payload["run_id"] == str(RUN_ID)
        assert payload["session_id"] == str(SESSION_ID)
        assert payload["run_type"] == "paper"
        assert payload["sealed_at"] == "2026-10-09T20:00:00+00:00"
        assert payload["closed_trade_count"] == 3
        assert payload["open_positions"] == []
        assert payload["open_positions_disposition"] == NONE_OPEN_DISPOSITION
        conditions = payload["execution_conditions"]
        assert conditions["initial_capital"] == "100000.00"
        assert conditions["start_date"] == "2026-09-01T13:00:00+00:00"
        assert conditions["end_date"] == "2026-10-09T20:00:00+00:00"
        assert conditions["data_source"] == PAPER_DATA_SOURCE

    async def test_the_curve_is_summarised_not_printed(self, harness):
        payload = seal_json_payload(await harness.seal())

        series = payload["execution_conditions"]["session_conditions"]["equity_series"]
        assert "curve" not in series
        assert series["curve_points"] == 3
        assert series["granularity"] == "closed_trade"

    async def test_the_closed_trade_count_is_the_series_own(self, harness):
        """One source for one number: the count the series was built from."""
        harness.result = _result(total_trades=99)

        report = await harness.seal()

        assert report.closed_trade_count == EQUITY_SERIES["closed_trades"]

    async def test_a_linked_backtest_warning_appears_in_both_forms(self, harness):
        harness.row = _row(linked_backtest_run_id=LINKED_RUN_ID)
        harness.linked = SimpleNamespace(execution_status="failed", metrics=None)

        report = await harness.seal()
        payload = seal_json_payload(report)
        human = "\n".join(render_seal_report(report))

        assert payload["linked_backtest_run_id"] == str(LINKED_RUN_ID)
        assert payload["linked_backtest_warning"] == report.linked_backtest_warning
        assert f"linked_backtest_warning: {report.linked_backtest_warning}" in human


def _keys_at_every_level(payload: dict) -> set[str]:
    keys = set(payload)
    for value in payload.values():
        if isinstance(value, dict):
            keys |= _keys_at_every_level(value)
    return keys


def _module_tree(relative: str) -> ast.Module:
    return ast.parse((PROJECT_ROOT / relative).read_text(encoding="utf-8"))


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


class TestARunRowExistsOnlyFromSealOnward:
    """D-I / AR5, pinned against the code both ways: a new path that writes a
    run row goes red, and so does the seal losing its own."""

    WRITERS = frozenset({"save_backtest_results", "save_failed_backtest", "create_backtest_run"})

    def _scan(self) -> tuple[set[str], set[str]]:
        callers: set[str] = set()
        constructors: set[str] = set()
        for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
            relative = str(path.relative_to(PROJECT_ROOT))
            names = _called_names(ast.parse(path.read_text(encoding="utf-8")))
            if names & self.WRITERS:
                callers.add(relative)
            if "BacktestRun" in names:
                constructors.add(relative)
        return callers, constructors

    @pytest.mark.acceptance_criterion(
        "5.3f", "no path creates a backtest_runs row at session creation or start", SOURCE
    )
    def test_only_the_backtest_paths_and_the_seal_write_a_run_row(self):
        callers, constructors = self._scan()

        assert callers == {
            "src/core/backtest_orchestrator.py",
            "src/core/backtest_runner.py",
            "src/services/backtest_persistence.py",
            SEAL_MODULE,
        }
        assert constructors == {
            "src/db/repositories/backtest_repository.py",
            "src/db/repositories/backtest_repository_sync.py",
        }

    def test_the_scan_is_not_vacuous(self):
        callers, _ = self._scan()

        assert "src/core/backtest_orchestrator.py" in callers
        assert "save_backtest_results" in _called_names(_module_tree(SEAL_MODULE))


#: Story 5.5 (AR31): how a Nautilus ``Position`` or ``Order`` is changed in place —
#: by applying an event to it. The seal reads both and must never call it.
POSITION_MUTATORS = frozenset({"apply"})
#: The seal's read of what the session holds (Story 5.5): on the seal path too.
SESSION_VIEW_MODULE = "src/core/live_session_view.py"


class TestTheSealReachesNoBroker:
    """D-J (AR31, NFR14, PO ruling R2a): nothing on the seal path can flatten,
    close or modify a position or an order, or open a broker connection."""

    MODULES = (SEAL_MODULE, SEAL_CLI_MODULE, SESSION_VIEW_MODULE)
    BROKER_IMPORTS = ("ibapi", "nautilus_trader.adapters", "nautilus_trader.live")

    @pytest.mark.acceptance_criterion(
        "5.5d",
        "no seal path flattens, closes or modifies a position — sealing never manufactures an exit",
        SOURCE,
    )
    @pytest.mark.parametrize("relative", MODULES)
    def test_no_order_method_or_flatten_is_called(self, relative):
        called = _called_names(_module_tree(relative))

        assert called, "the scan found no call; it is not reading the module"
        assert not called & FORBIDDEN_ORDER_METHODS
        assert not called & POSITION_MUTATORS
        assert not [name for name in called if FORBIDDEN_FLATTEN_FRAGMENT in name.lower()]

    @pytest.mark.parametrize(
        "planted",
        [
            "strategy.close_position(position)",
            "self.close_all_positions(instrument_id)",
            "strategy.cancel_order(order)",
            "position.apply(fill)",
            "flatten_everything()",
        ],
    )
    def test_the_scan_can_fail(self, planted):
        called = _called_names(ast.parse(planted))

        assert called & (FORBIDDEN_ORDER_METHODS | POSITION_MUTATORS) or [
            name for name in called if FORBIDDEN_FLATTEN_FRAGMENT in name.lower()
        ]

    async def test_a_forced_seal_writes_only_open_trade_rows_and_links(self, harness):
        """Task 5.2: the repository calls a forced seal makes are the read, the
        open-row insert and the backfill — and the state machine's own lookups."""
        harness.holdings = _holding(_leg("AAPL.NASDAQ", "10"))

        await harness.seal(force=True)

        called = {name for name, _, _ in harness.repository.method_calls}
        assert "insert_trade_if_absent" in called, "the forced seal wrote no open row"
        assert called <= {
            "find_by_name",
            "find_by_session_id",
            "insert_trade_if_absent",
            "find_session_trades",
            "backfill_trades_run_id",
        }

    @pytest.mark.parametrize("relative", MODULES)
    def test_no_broker_module_is_imported(self, relative):
        imported: set[str] = set()
        for node in ast.walk(_module_tree(relative)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert imported, "the import scan found nothing; it is not reading the module"
        assert not [name for name in imported if name.startswith(self.BROKER_IMPORTS)]
