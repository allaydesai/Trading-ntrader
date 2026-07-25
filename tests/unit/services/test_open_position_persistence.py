"""Unit tests for persisting positions that are still open at the end of a run.

A backtest that finishes holding a position used to persist its run row and its
metrics, and then silently no trades at all — the positions report carries NaN in
every exit field for an open position, and the writer skipped those rows at debug
level. The trades schema was always built for this case (``exit_price`` and
``exit_timestamp`` are nullable with a CHECK that tolerates NULL), so the fix is to
record the trade without an exit rather than drop it.

These tests build the positions DataFrame the way pandas actually produces it: as
soon as one position is open, ``duration_ns`` and ``avg_px_close`` become float64
columns carrying NaN. That matters because ``float('nan')`` is truthy, so the old
``if row["duration_ns"]`` guards did not filter it.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from src.services.backtest_persistence import BacktestPersistenceService

RUN_ID = 1


def _positions_frame(*rows: dict) -> pd.DataFrame:
    """Build a positions report indexed by position id, as Nautilus emits it."""
    frame = pd.DataFrame(list(rows))
    return frame.set_index("position_id")


def _closed_row(position_id: str = "P-1") -> dict:
    return {
        "position_id": position_id,
        "instrument_id": "IVV.ARCA",
        "opening_order_id": "O-1",
        "closing_order_id": "O-2",
        "entry": "BUY",
        "peak_qty": 10,
        "avg_px_open": 100.0,
        "avg_px_close": 110.0,
        "ts_opened": pd.Timestamp("2020-01-01", tz="UTC"),
        "ts_closed": pd.Timestamp("2020-01-10", tz="UTC"),
        "duration_ns": 777_600_000_000_000,
        "realized_pnl": "100.00 USD",
        "commissions": ["1.50 USD"],
    }


def _open_row(position_id: str = "P-2") -> dict:
    """An open position — exactly the shape Nautilus's report produces.

    ``Position.to_dict`` maps ts_closed/duration_ns/avg_px_close to None for an
    open position, and pandas then coerces the numeric columns to float64 NaN.
    """
    return {
        "position_id": position_id,
        "instrument_id": "IVV.ARCA",
        "opening_order_id": "O-3",
        "closing_order_id": None,
        "entry": "BUY",
        "peak_qty": 5,
        "avg_px_open": 120.0,
        "avg_px_close": float("nan"),
        "ts_opened": pd.Timestamp("2020-02-01", tz="UTC"),
        "ts_closed": pd.NaT,
        "duration_ns": float("nan"),
        "realized_pnl": None,
        "commissions": ["0.75 USD"],
    }


@pytest.fixture
def service():
    repository = AsyncMock()
    repository.bulk_create_trades = AsyncMock()
    return BacktestPersistenceService(repository), repository


async def _save(service_pair, frame) -> list:
    service, repository = service_pair
    count = await service.save_trades_from_positions(
        backtest_run_id=RUN_ID, positions_report_df=frame
    )
    saved = repository.bulk_create_trades.call_args.args[0] if count else []
    return saved


@pytest.mark.unit
class TestOpenPositions:
    """An open position is a trade without an exit, not a non-trade."""

    async def test_open_position_is_persisted(self, service):
        saved = await _save(service, _positions_frame(_open_row()))

        assert len(saved) == 1
        assert saved[0].trade_id == "P-2"

    async def test_open_position_has_null_exit_fields(self, service):
        saved = await _save(service, _positions_frame(_open_row()))

        trade = saved[0]
        assert trade.exit_price is None
        assert trade.exit_timestamp is None
        assert trade.profit_pct is None
        assert trade.holding_period_seconds is None

    async def test_open_position_keeps_its_entry_data(self, service):
        saved = await _save(service, _positions_frame(_open_row()))

        trade = saved[0]
        assert trade.entry_price == Decimal("120.00000000")
        assert trade.quantity == Decimal("5.00000000")
        assert trade.entry_timestamp == datetime(2020, 2, 1, tzinfo=timezone.utc)
        assert trade.order_side == "BUY"

    async def test_missing_closing_order_id_is_null_not_the_string_none(self, service):
        """str(None) would persist "None" — a plausible-looking id referring to nothing."""
        saved = await _save(service, _positions_frame(_open_row()))

        assert saved[0].client_order_id is None

    async def test_open_position_realized_pnl_is_zero(self, service):
        saved = await _save(service, _positions_frame(_open_row()))

        assert saved[0].profit_loss == Decimal("0.00")

    async def test_commission_is_still_captured(self, service):
        saved = await _save(service, _positions_frame(_open_row()))

        assert saved[0].commission_amount == Decimal("0.75")
        assert saved[0].commission_currency == "USD"


@pytest.mark.unit
class TestMixedReport:
    """The realistic case: a run with closed trades that ends holding one."""

    async def test_both_closed_and_open_are_saved(self, service):
        frame = _positions_frame(_closed_row(), _open_row())
        saved = await _save(service, frame)

        assert len(saved) == 2
        by_id = {t.trade_id: t for t in saved}
        assert by_id["P-1"].exit_price == Decimal("110.00000000")
        assert by_id["P-2"].exit_price is None

    async def test_closed_position_metrics_are_unaffected(self, service):
        """NaN in a sibling row coerces the column to float — the closed row must survive."""
        frame = _positions_frame(_closed_row(), _open_row())
        saved = await _save(service, frame)

        closed = next(t for t in saved if t.trade_id == "P-1")
        assert closed.holding_period_seconds == 777_600
        assert closed.profit_loss == Decimal("100.00")
        assert closed.profit_pct == Decimal("10")
        assert closed.exit_timestamp == datetime(2020, 1, 10, tzinfo=timezone.utc)

    async def test_return_count_includes_open_positions(self, service):
        svc, _ = service
        count = await svc.save_trades_from_positions(
            backtest_run_id=RUN_ID, positions_report_df=_positions_frame(_closed_row(), _open_row())
        )
        assert count == 2

    async def test_all_open_still_writes_trades(self, service):
        """The regression: a run ending entirely on open positions wrote nothing."""
        frame = _positions_frame(_open_row("P-A"), _open_row("P-B"))
        saved = await _save(service, frame)

        assert len(saved) == 2


@pytest.mark.unit
class TestNaNGuards:
    """float('nan') is truthy — the old guards did not filter it."""

    async def test_nan_duration_does_not_raise(self, service):
        """int(nan) raises ValueError: cannot convert float NaN to integer."""
        saved = await _save(service, _positions_frame(_open_row()))
        assert saved[0].holding_period_seconds is None

    async def test_nan_exit_price_does_not_raise(self, service):
        """Decimal('NaN').quantize() raises InvalidOperation."""
        saved = await _save(service, _positions_frame(_open_row()))
        assert saved[0].exit_price is None

    async def test_zero_duration_is_recorded_as_zero_not_dropped(self, service):
        """A same-instant close is 0, which is falsy — it must not be read as absent."""
        row = _closed_row()
        row["duration_ns"] = 0
        saved = await _save(service, _positions_frame(row))

        assert saved[0].holding_period_seconds == 0
