"""A sealed paper record beside its backtest, in Postgres — Story 5.6's fixture.

The two **run records** come through their real writers: the backtest's run and
metrics through ``BacktestPersistenceService.save_backtest_results`` (the path
every backtest takes), the paper record through Story 5.3's ``seal_session``
over a session moved through the real state machine. The **trade rows** are
hand-mapped (code review P4): the session's mirror the columns
``SqlTradeRecord.persist`` writes (Story 3.6), inserted through the recorder's
own ``insert_trade_if_absent``; the backtest's mirror the shape
``save_trades_from_positions`` writes. Neither production formula runs here —
that both store the same ``profit_pct`` sign is pinned at the component tier
(``tests/component/services/test_profit_pct_parity.py``). The seal is forced
over two open legs — the strategy's own and a reconciliation-owned one (Story
5.5, PO ruling R1a) — so the record carries null-exit rows too. The holdings
reader is a double: the real one reads Redis and is proven against a real Redis
elsewhere (``tests/integration/core/test_live_session_view_redis.py``).

Every number below is chosen to be computed by hand, so a view that renders
the wrong unit or the wrong field cannot pass by agreeing with itself (D-C).

The read side is reached the way an operator reaches it: the FastAPI app
in-process (``asgi_client``) with ``get_db`` overridden to the test session,
and the Click commands with their ``get_sync_session`` pointed at the same
schema (``sync_session_on``). Nothing under ``src/api/**``, ``templates/**`` or
the CLI views is modified (AR44).
"""

import html
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import get_db
from src.api.web import app
from src.config import get_settings
from src.db.models.trade import Trade
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.trading_session_repository import TradingSessionRepository
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.backtest_result import BacktestResult
from src.models.reconciliation import OpenLeg, SessionHoldings, SessionView, ViewPosition
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.session_seal_service import seal_session
from src.services.session_service import SessionService

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
CAPITAL = Decimal("100000.00")
INSTRUMENT = "AAPL.NASDAQ"
BAR_TYPE = f"{INSTRUMENT}-1-MINUTE-LAST-EXTERNAL"
SPEC = SessionSpec(
    strategies=(StrategySpec(strategy_id="sma_crossover", bar_types=(BAR_TYPE,)),)
).to_stored()

#: The session's closed legs, 100 shares each: (side, entry, exit, P&L). Two
#: winners — one a profitable SHORT (debt item 1) — and two losers, a day apart.
#: P&L is the row's ``profit_loss`` — its realized P&L, already net of commission
#: as Nautilus books it — and the seal sums it as is (code review P16).
#: P&L +800 on 100,000 → total return 0.008 ("0.80%"), final 100,800.00, win
#: rate 2/4, profit factor 1500/700. Per-trade price returns +10%, −4%, −3%,
#: +5% compound to a peak of 1.10 and a trough of 1.02432: max drawdown −6.88%
#: (the metric's basis, 5.2's ruling R1b). The account itself goes 100,000 →
#: 101,000 → 100,600 → 100,300 → 100,800: the trades-based drawdown is 700.
PAPER_LEGS = (
    ("BUY", "100", "110", "1000"),
    ("SELL", "100", "104", "-400"),
    ("BUY", "100", "97", "-300"),
    ("SELL", "100", "95", "500"),
)

#: The backtest's metrics, as the backtest path hands them to the persistence
#: service. Win rate is the service's own: 2/3.
BACKTEST_RESULT = BacktestResult(
    total_return=0.25,
    final_balance=125000.0,
    total_trades=3,
    winning_trades=2,
    losing_trades=1,
    sharpe_ratio=1.85,
    sortino_ratio=2.1,
    volatility=0.18,
    profit_factor=2.5,
    max_drawdown=-0.12,
    cagr=0.0283,
    calmar_ratio=0.2358,
)
#: The backtest's closed rows (side, entry, exit, P&L) — one a profitable SHORT —
#: and its one position open at the end of data (a null-exit row).
BACKTEST_LEGS = (
    ("BUY", "50", "60", "1000"),
    ("SELL", "80", "70", "1000"),
    ("BUY", "40", "38", "-200"),
)
BACKTEST_PERIOD = (datetime(2016, 1, 4, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC))


@dataclass(frozen=True)
class SealedPair:
    """Plain values read back from Postgres after the seal committed."""

    session_pk: int
    paper_run_id: UUID
    paper_pk: int
    backtest_run_id: UUID
    backtest_pk: int

    @property
    def run_ids(self) -> tuple[UUID, UUID]:
        return self.paper_run_id, self.backtest_run_id


def profit_pct(entry: Decimal, exit_: Decimal) -> Decimal:
    """The backtest formula both paths use, side-unaware (debt item 1)."""
    return (exit_ - entry) / entry * Decimal("100")


def _session_row(session_pk: int, index: int, leg: tuple, closed_at: datetime) -> Trade:
    """One closed row, mapped as ``SqlTradeRecord.persist`` maps it (Story 3.6)."""
    side, entry, exit_, pnl = leg
    return Trade(
        backtest_run_id=None,
        session_id=session_pk,
        instrument_id=INSTRUMENT,
        trade_id=f"{INSTRUMENT}-SMACrossover-000",
        venue_order_id=f"V-{index}",
        client_order_id=f"O-close-{index}",
        order_side=side,
        quantity=Decimal("100"),
        entry_price=Decimal(entry),
        exit_price=Decimal(exit_),
        commission_amount=Decimal("1.00"),
        commission_currency="USD",
        fees_amount=Decimal("0"),
        entry_timestamp=closed_at - timedelta(hours=2),
        exit_timestamp=closed_at,
        profit_loss=Decimal(pnl),
        profit_pct=profit_pct(Decimal(entry), Decimal(exit_)),
        holding_period_seconds=7200,
    )


def _backtest_row(backtest_pk: int, index: int, leg: tuple | None) -> Trade:
    """A row in ``save_trades_from_positions``' shape; ``None`` is the open one."""
    opened_at = BACKTEST_PERIOD[0] + timedelta(days=30 * (index + 1))
    common = dict(
        backtest_run_id=backtest_pk,
        instrument_id=INSTRUMENT,
        trade_id=f"{INSTRUMENT}-SMACrossover-000-{index}",
        venue_order_id=f"BT-V-{index}",
        quantity=Decimal("100"),
        commission_amount=Decimal("1.00"),
        commission_currency="USD",
        fees_amount=Decimal("0.00"),
        entry_timestamp=opened_at,
    )
    if leg is None:
        return Trade(
            **common,
            client_order_id=None,
            order_side="BUY",
            entry_price=Decimal("45"),
            exit_price=None,
            exit_timestamp=None,
            profit_loss=Decimal("-1.00"),
            profit_pct=None,
            holding_period_seconds=None,
        )
    side, entry, exit_, pnl = leg
    return Trade(
        **common,
        client_order_id=f"BT-O-{index}",
        order_side=side,
        entry_price=Decimal(entry),
        exit_price=Decimal(exit_),
        exit_timestamp=opened_at + timedelta(days=5),
        profit_loss=Decimal(pnl),
        profit_pct=profit_pct(Decimal(entry), Decimal(exit_)),
        holding_period_seconds=5 * 86400,
    )


async def write_backtest(db: AsyncSession) -> UUID:
    """The linked backtest, through the backtest path's own writer, committed."""
    run_id = uuid4()
    run = await BacktestPersistenceService(BacktestRepository(db)).save_backtest_results(
        run_id=run_id,
        strategy_name="Sma Crossover",
        strategy_type="sma_crossover",
        instrument_symbol="AAPL",
        start_date=BACKTEST_PERIOD[0],
        end_date=BACKTEST_PERIOD[1],
        initial_capital=CAPITAL,
        data_source="catalog",
        execution_duration_seconds=Decimal("12.5"),
        config_snapshot={
            "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
            "config_path": "src.core.strategies.sma_crossover:SMAConfig",
            "version": "1.0",
            "config": {"fast_period": 10, "slow_period": 20},
        },
        backtest_result=BACKTEST_RESULT,
    )
    legs: tuple = (*BACKTEST_LEGS, None)
    db.add_all([_backtest_row(run.id, index, leg) for index, leg in enumerate(legs)])
    await db.commit()
    return run_id


def open_leg(quantity: str, owner: str) -> OpenLeg:
    """An open position the session's cache holds at seal (Story 5.5)."""
    signed = Decimal(quantity)
    return OpenLeg(
        position_id=f"{INSTRUMENT}-{owner}",
        instrument_id=INSTRUMENT,
        strategy_id=owner,
        side="BUY" if signed > 0 else "SELL",
        quantity=signed,
        avg_px_open=Decimal("187.25000000"),
        opened_at=datetime(2026, 9, 2, 14, 30, tzinfo=UTC),
        opening_order_id=f"O-open-{owner}",
        realized_pnl=Decimal("-1.00"),
        commission_amount=Decimal("1.00"),
        commission_currency="USD",
    )


#: The strategy's own long and a reconciliation-owned one beside it (R1a).
OPEN_LEGS = (open_leg("10", "SMACrossover-000"), open_leg("5", "EXTERNAL"))


def _held(_session_id: UUID) -> SessionHoldings:
    net = sum((leg.quantity for leg in OPEN_LEGS), Decimal(0))
    view = SessionView("PAPER-held", (ViewPosition(INSTRUMENT, net),), ())
    return SessionHoldings(view=view, legs=OPEN_LEGS)


async def _stopped_session(db: AsyncSession, name: str, linked: UUID | None):
    row = await TradingSessionRepository(db).create(
        name=name, spec=SPEC, linked_backtest_run_id=linked
    )

    def move(sync_session):
        service = SessionService(SyncTradingSessionRepository(sync_session))
        for target in (SessionStatus.RUNNING, SessionStatus.STOPPED):
            service.transition(row.session_id, to=target)

    await db.run_sync(move)
    return row.id, row.created_at


async def seal_paper(
    db: AsyncSession, *, name: str, legs: tuple, linked: UUID | None = None
) -> int:
    """A stopped session with ``legs`` as its closed rows, force-sealed over
    :data:`OPEN_LEGS` and committed. Returns the session's pk."""
    session_pk, created_at = await _stopped_session(db, name, linked)
    repository = TradingSessionRepository(db)
    for index, leg in enumerate(legs):
        closed_at = created_at + timedelta(days=index + 1)
        await repository.insert_trade_if_absent(_session_row(session_pk, index, leg, closed_at))
    await db.commit()
    sealed_at = created_at + timedelta(days=len(legs) + 2)
    await seal_session(
        db,
        name,
        starting_capital=CAPITAL,
        read_open_positions=_held,
        force=True,
        clock=lambda: sealed_at,
        new_run_id=uuid4,
    )
    await db.commit()
    return session_pk


async def seal_beside_backtest(db: AsyncSession) -> SealedPair:
    """The linked backtest, then the sealed session, ids read back from Postgres."""
    linked = await write_backtest(db)
    session_pk = await seal_paper(db, name="paper-beside-backtest", legs=PAPER_LEGS, linked=linked)
    db.expire_all()
    session = (
        await db.execute(
            text(
                "SELECT sealed_run_id, linked_backtest_run_id FROM trading_sessions WHERE id = :pk"
            ),
            {"pk": session_pk},
        )
    ).one()
    rows = await db.execute(
        text("SELECT run_id, id FROM backtest_runs WHERE run_id IN (:a, :b)"),
        {"a": session.sealed_run_id, "b": session.linked_backtest_run_id},
    )
    pks: dict[UUID, int] = {row.run_id: row.id for row in rows}
    return SealedPair(
        session_pk=session_pk,
        paper_run_id=session.sealed_run_id,
        paper_pk=pks[session.sealed_run_id],
        backtest_run_id=session.linked_backtest_run_id,
        backtest_pk=pks[session.linked_backtest_run_id],
    )


@asynccontextmanager
async def asgi_client(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """The real app in-process, every request on the test session."""

    async def override_get_db():
        yield db

    # Only this entry is ours to set and restore (code review P5).
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    try:
        # Redirects followed, as a browser follows the nav's ``/backtests`` → ``/backtests/``.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=True
        ) as client:
            yield client
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


async def schema_of(db: AsyncSession) -> str:
    """The per-worker schema the ``db_session`` fixture set as its search path."""
    return str((await db.execute(text("SHOW search_path"))).scalar_one())


@contextmanager
def sync_session_on(schema: str, time_zone: str | None = None) -> Iterator[Session]:
    """A pg8000 session on ``schema`` — what a CLI view's ``get_sync_session`` yields
    (pure Python: no libpq to crash in a ``--forked`` child).

    pg8000 returns a ``timestamptz`` in the session's own zone, where asyncpg
    returns UTC (code review P1): ``time_zone`` sets it, so a test can hold a
    CLI view to a date far from UTC's whatever the hour it runs at. ``None``
    keeps the server's default, as the CLI itself runs.
    """
    database_url = get_settings().database_url
    assert database_url, "DATABASE_URL is unset; the db_session fixture would have skipped"
    url = database_url.replace("postgresql://", "postgresql+pg8000://")
    engine = create_engine(url)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            session.execute(text(f"SET search_path TO {schema}"))
            if time_zone is not None:
                session.execute(text(f"SET TIME ZONE '{time_zone}'"))
            yield session
    finally:
        engine.dispose()


def visible_text(fragment: str) -> str:
    """A fragment's text as a reader sees it: tags dropped, entities decoded,
    whitespace collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def short(run_id: UUID) -> str:
    """The views' short id: the UUID's first eight characters."""
    return str(run_id)[:8]
