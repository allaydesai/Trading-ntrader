"""Monte Carlo trades carry the days since the previous exit (S4.1)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.mcp_server.paper.band import ClosedTrade
from src.mcp_server.studies.variability import mc_trades

pytestmark = pytest.mark.unit

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _trade(entry_day: float, exit_day: float, pnl: float) -> ClosedTrade:
    return ClosedTrade(
        entry_at=T0 + timedelta(days=entry_day),
        exit_at=T0 + timedelta(days=exit_day),
        ret=pnl / 10_000,
        pnl=pnl,
        commission_frac=None,
    )


def test_gaps_run_from_the_run_start_and_sum_to_the_last_exit():
    trades = [_trade(30, 40, 100.0), _trade(5, 12, -50.0), _trade(35, 45.5, 20.0)]
    converted = mc_trades(trades, run_start=T0)
    assert [t.pnl for t in converted] == [-50.0, 100.0, 20.0]  # exit order
    assert [t.gap_days for t in converted] == [12.0, 28.0, 5.5]
    assert sum(t.gap_days for t in converted) == 45.5


def test_an_exit_before_the_run_start_gives_a_zero_gap():
    assert mc_trades([_trade(0, 1, 1.0)], run_start=T0 + timedelta(days=3))[0].gap_days == 0.0
