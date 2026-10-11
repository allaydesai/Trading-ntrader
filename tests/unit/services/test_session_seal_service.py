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

from src.core.live_session_view import SessionViewFailure, SessionViewUnavailableError
from src.core.session_equity import EQUITY_SERIES_KEY, SessionResults
from src.core.strategy_registry import StrategyRegistry
from src.db.exceptions import InvalidSessionTransition, SealRefusedError
from src.db.models.backtest import BacktestRun
from src.db.models.trading_session import TradingSession
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.backtest_result import BacktestResult
from src.models.reconciliation import SessionView, ViewPosition
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services import session_seal_service as seal_module
from src.services.session_conditions import (
    AR7_KEYS,
    LAST_RUN_FLAGS_KEY,
    PAPER_PRICE_BASIS,
    START_DATE_BASIS,
    START_DATE_BASIS_KEY,
)
from src.services.session_seal_service import (
    INSTRUMENT_SYMBOL_MAX,
    LINKED_BACKTEST_WARNING_EVENT,
    NONE_OPEN_DISPOSITION,
    PAPER_DATA_SOURCE,
    PAPER_RUN_TYPE,
    period_start,
    render_seal_report,
    require_sealable_capital,
    run_identity,
    seal_json_payload,
    seal_session,
    strategy_display_name,
)
from src.services.session_service import SessionService
from tests.unit.core.test_live_stop_path_is_inert import (
    FORBIDDEN_FLATTEN_FRAGMENT,
    FORBIDDEN_ORDER_METHODS,
)

pytestmark = pytest.mark.unit

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SEAL_MODULE = "src/services/session_seal_service.py"
SEAL_CLI_MODULE = "src/cli/commands/live_seal.py"
#: Story 5.4: on the seal path, so hand-listed in the broker pins below (D-F).
CONDITIONS_MODULE = "src/services/session_conditions.py"
#: Story 5.4's ACs 7–9 are the story's own, not the PRD's (code review P12).
STORY = (
    "_bmad-output/implementation-artifacts/"
    "5-4-record-the-conditions-the-session-actually-ran-under.md"
)
AC_8 = (
    "a synthetic owner's split shares held after the strategy exits are an open position at "
    "seal: the seal refuses naming them, and open_positions_at_seal is built from the same view"
)

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
    view: SessionView = field(default_factory=lambda: SessionView("PAPER-33333333", (), ()))
    trades: list = field(default_factory=lambda: ["trade-1", "trade-2", "trade-3"])
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

    def _read(self, session_id: UUID) -> SessionView:
        self.events.append("read_view")
        return self.view

    def _trades(self, session_pk: int) -> list:
        self.events.append("find_trades")
        return list(self.trades)

    def _backfill(self, session_pk: int, run_pk: int) -> int:
        self.events.append("backfill")
        return len(self.trades) if self.backfilled is None else self.backfilled

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

    async def seal(self, *, clock=None, capital: Decimal = CAPITAL):
        return await seal_session(
            self.db,
            "paper-alpha",
            starting_capital=capital,
            read_open_positions=self.reader,
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
        harness.view = SessionView(
            "PAPER-33333333", (ViewPosition("AAPL.NASDAQ", Decimal("-25")),), ()
        )

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
        harness.view = SessionView(
            "PAPER-33333333", (ViewPosition("AAPL.NASDAQ", Decimal("10")),), ()
        )

        with pytest.raises(SealRefusedError) as raised:
            await harness.seal()

        message = str(raised.value)
        assert "restart the session" in message
        assert "broker" not in message
        assert "flatten" not in message

    async def test_positions_on_another_account_refuse(self, harness):
        harness.view = SessionView("PAPER-33333333", (), (), positions_skipped=2)

        with pytest.raises(SealRefusedError, match="another account"):
            await harness.seal()

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
        """Widened deliberately by Story 5.4: ``session_conditions`` was
        ``{"equity_series": …}`` alone; AR7's keys and the two ruled extras now
        sit beside the series, which is unchanged. The exact set is pinned —
        never a subset — so a key added or lost on either side goes red."""
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
        conditions = snapshot["session_conditions"]
        assert set(conditions) == set(AR7_KEYS) | {
            START_DATE_BASIS_KEY,
            LAST_RUN_FLAGS_KEY,
            EQUITY_SERIES_KEY,
        }
        assert conditions[EQUITY_SERIES_KEY] == EQUITY_SERIES

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


#: The last process run's ``runtime_flags`` as ``SessionService`` stores them.
RUNTIME_FLAGS = {
    "v": 1,
    "all_failed": False,
    "failed_strategies": [
        {
            "strategy_id": "SMACrossover-000",
            "spec_strategy_id": "sma_crossover",
            "error_type": "ValueError",
            "handler": "on_bar",
            "at": "2026-10-02T15:30:00+00:00",
            "detail": "indicator not ready",
        }
    ],
}


class TestTheConditionsTheSessionRanUnder:
    """Story 5.4 (FR43, FR45, AR7): the record carries what produced it."""

    def _stored(self, harness) -> dict[str, Any]:
        return harness.save_kwargs["config_snapshot"]["session_conditions"]

    @pytest.mark.acceptance_criterion(
        "5.4a",
        "a sealed session's config_snapshot holds session_conditions with starting_capital, "
        "rth_only, price_basis, market_data_tier, closed_trade_count, open_positions_at_seal "
        "and schema_version: 1",
        SOURCE,
    )
    async def test_the_written_snapshot_states_every_ar7_condition(self, harness):
        await harness.seal()

        conditions = self._stored(harness)
        assert conditions["starting_capital"] == "100000.00"
        assert conditions["rth_only"] is True
        assert conditions["price_basis"] == PAPER_PRICE_BASIS
        assert conditions["market_data_tier"] == "REALTIME"
        assert conditions["closed_trade_count"] == EQUITY_SERIES["closed_trades"]
        assert conditions["open_positions_at_seal"] == []
        assert conditions["schema_version"] == 1

    @pytest.mark.acceptance_criterion(
        "5.4c",
        "the closed-trade count travels with the record so a difference can be read against "
        "sampling noise",
        SOURCE,
    )
    async def test_the_closed_trade_count_is_the_series_own_in_both_places(self, harness):
        """One computation, two keys, pinned equal (5.2's deferred pin). Five
        rows read, the series built from the three closed ones: the record says
        three — never the five rows read."""
        harness.trades = ["trade-1", "trade-2", "trade-3", "open-4", "open-5"]

        report = await harness.seal()

        conditions = self._stored(harness)
        assert conditions["closed_trade_count"] == conditions[EQUITY_SERIES_KEY]["closed_trades"]
        assert report.closed_trade_count == conditions["closed_trade_count"] == 3
        assert report.session_conditions["closed_trade_count"] == 3
        assert harness.save_kwargs["backtest_result"].total_trades == 3

    async def test_metrics_that_count_other_trades_than_the_series_refuse(self, harness):
        """Code review P5: ``performance_metrics.total_trades`` and
        ``closed_trade_count`` describe one sequence. If they ever disagree the
        record would contradict itself, so the seal refuses, naming both — the
        backfill-count precedent."""
        harness.result = _result(total_trades=99)

        with pytest.raises(SealRefusedError, match=r"99 trade\(s\).*3 closed trade\(s\)"):
            await harness.seal()

        harness.save.assert_not_called()

    async def test_a_key_the_results_would_overwrite_refuses(self, harness):
        """Code review P7: ``merged_into`` guards only ``equity_series``; a
        results key named like a condition would silently replace it."""
        harness.reconstruct.side_effect = lambda trades, **kwargs: SessionResults(
            harness.result, {EQUITY_SERIES_KEY: dict(EQUITY_SERIES), "price_basis": "other"}
        )

        with pytest.raises(SealRefusedError, match="price_basis"):
            await harness.seal()

        harness.save.assert_not_called()

    @pytest.mark.parametrize(
        "stored", [["odd", {"v": 1}], "a string", {}], ids=["list", "str", "empty"]
    )
    async def test_flags_of_any_stored_shape_still_seal(self, harness, stored):
        """Code review P4: a stopped session must always be sealable; a
        hand-edited or restored ``runtime_flags`` is recorded as stored (R2a)."""
        harness.row = _row(runtime_flags=stored)

        await harness.seal()

        assert self._stored(harness)[LAST_RUN_FLAGS_KEY] == stored

    async def test_the_last_runs_flags_are_copied_from_the_locked_row(self, harness):
        """PO ruling R2a: the sealed row's ``runtime_flags``, as stored — a copy,
        so nothing that later touches the row can change the record."""
        harness.row = _row(runtime_flags=json.loads(json.dumps(RUNTIME_FLAGS)))

        await harness.seal()

        recorded = self._stored(harness)[LAST_RUN_FLAGS_KEY]
        assert recorded == RUNTIME_FLAGS
        assert recorded is not harness.row.runtime_flags

    async def test_a_row_with_no_flags_records_null(self, harness):
        await harness.seal()

        assert self._stored(harness)[LAST_RUN_FLAGS_KEY] is None

    @pytest.mark.acceptance_criterion(
        "5.4e",
        "the paper session's own starting balance is stored as-is, with no normalization",
        SOURCE,
    )
    async def test_the_capital_is_the_operators_never_the_linked_backtests(self, harness):
        """AC #5: comparability comes from matching the accounts, not from
        arithmetic — a linked backtest run on a different balance changes
        nothing about this record's."""
        harness.row = _row(linked_backtest_run_id=LINKED_RUN_ID)
        harness.linked = SimpleNamespace(
            execution_status="success", metrics=object(), initial_capital=Decimal("50000.00")
        )

        await harness.seal(capital=Decimal("1E+5"))

        stored = self._stored(harness)["starting_capital"]
        assert stored == "100000.00" == str(harness.db.run.initial_capital)
        assert harness.save_kwargs["initial_capital"] == Decimal("100000")

    @pytest.mark.acceptance_criterion(
        "5.4d",
        "initial_capital matches the starting capital, start_date is the session's creation "
        "(R1a, recorded as start_date_basis), end_date is the seal time, data_source "
        "identifies the live IBKR source",
        SOURCE,
    )
    async def test_the_run_rows_own_columns_and_their_stated_basis(self, harness):
        await harness.seal()

        kwargs = harness.save_kwargs
        conditions = self._stored(harness)
        assert kwargs["initial_capital"] == Decimal(conditions["starting_capital"])
        assert kwargs["start_date"] == CREATED
        assert conditions[START_DATE_BASIS_KEY] == START_DATE_BASIS == "session_created_at"
        assert kwargs["end_date"] == SEALED_AT == harness.row.sealed_at
        assert kwargs["data_source"] == PAPER_DATA_SOURCE == "ibkr_live"

    @pytest.mark.acceptance_criterion("5.4h", AC_8, STORY)
    async def test_a_synthetic_owners_split_shares_refuse_the_seal_by_name(self, harness):
        """Story 4.7's debt: the strategy sold its 10, the split's other 10 sit
        with ``EXTERNAL``. The view nets every owner, so the seal sees them,
        refuses (R2a) and names them — never "none open at seal"."""
        harness.view = SessionView(
            "PAPER-33333333", (ViewPosition("AAPL.NASDAQ", Decimal("10")),), ()
        )

        with pytest.raises(SealRefusedError, match=r"AAPL\.NASDAQ 10"):
            await harness.seal()

        harness.save.assert_not_called()

    async def test_a_condition_the_builder_refuses_becomes_a_seal_refusal(
        self, harness, monkeypatch
    ):
        cause = ValueError("the starting capital 1.005 has fractions of a cent")

        def refuse(**kwargs):
            raise cause

        monkeypatch.setattr(seal_module, "build_session_conditions", refuse)

        with pytest.raises(SealRefusedError, match="fractions of a cent") as raised:
            await harness.seal()

        assert raised.value.__cause__ is cause
        harness.save.assert_not_called()


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
        "open_positions",
        "open_positions_disposition",
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
        """One source for one number: the count the series was built from —
        not the rows read. (Story 5.4's code review P5 made a result counting
        other trades a refusal, so the series and result now agree here.)"""
        harness.trades = ["trade-1", "trade-2", "trade-3", "open-4"]

        report = await harness.seal()

        assert report.closed_trade_count == EQUITY_SERIES["closed_trades"]

    async def test_stored_flags_of_any_shape_render_one_line_per_key(self, harness):
        """Code review P2/P3: ``last_run_flags`` is arbitrary stored JSONB, so
        the human form must render any JSON shape — a mixed list, an empty
        mapping, free text with a newline — without raising (it renders after
        the commit) and without a line that reads as a top-level key."""
        harness.row = _row(
            runtime_flags={
                "v": 1,
                "failed_strategies": [{"strategy_id": "S-1"}, "garbage", 7],
                "order_rejections": {"last": {"reason": "rejected\nline two: market shut"}},
                "empty": {},
            }
        )

        report = await harness.seal()
        lines = render_seal_report(report)

        top = {line.split(":", 1)[0] for line in lines if not line.startswith(" ")}
        assert top == set(seal_json_payload(report))
        human = "\n".join(lines)
        assert "- garbage" in human and "- 7" in human
        assert "reason: rejected\\nline two: market shut" in human
        assert "empty: {}" in human

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


class TestTheSealReachesNoBroker:
    """D-J (AR31, NFR14, PO ruling R2a): nothing on the seal path can flatten,
    close or modify a position or an order, or open a broker connection."""

    MODULES = (SEAL_MODULE, SEAL_CLI_MODULE, CONDITIONS_MODULE)
    BROKER_IMPORTS = ("ibapi", "nautilus_trader.adapters", "nautilus_trader.live")

    @pytest.mark.parametrize("relative", MODULES)
    def test_no_order_method_or_flatten_is_called(self, relative):
        called = _called_names(_module_tree(relative))

        assert not called & FORBIDDEN_ORDER_METHODS
        assert not [name for name in called if FORBIDDEN_FLATTEN_FRAGMENT in name.lower()]

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
