"""The session's clock and its G4 progress: pure parts of the ``get_session`` report."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.paper import report
from src.mcp_server.paper.band import ClosedTrade
from src.mcp_server.paper.handoff import _standing_warnings
from src.mcp_server.paper.report import _paper_stats, check_horizon, g4_progress, horizon

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)


def _row(**overrides):
    fields = dict(
        status="running",
        created_at=NOW - timedelta(weeks=10),
        last_started_at=NOW - timedelta(weeks=10),
        last_stopped_at=None,
        sealed_at=None,
    )
    return SimpleNamespace(**{**fields, **overrides})


def test_weeks_run_from_creation_so_a_restart_does_not_reset_them():
    restarted = _row(
        last_started_at=NOW - timedelta(days=1), last_stopped_at=NOW - timedelta(days=2)
    )
    hz = horizon(restarted, NOW, None)
    assert hz["weeks"] == 10.0
    assert hz["includes_stopped_time"] is True
    assert horizon(_row(), NOW, None)["includes_stopped_time"] is False


def test_a_session_never_started_has_run_for_no_time():
    hz = horizon(_row(status="created", last_started_at=None), NOW, None)
    assert hz["weeks"] == 0.0 and hz["band_days"] == 1


def test_a_stopped_session_stops_its_clock():
    stopped = _row(status="stopped", last_stopped_at=NOW - timedelta(weeks=4))
    assert horizon(stopped, NOW, None)["weeks"] == 6.0


def test_weeks_never_round_up_past_a_minimum():
    almost = _row(created_at=NOW - timedelta(weeks=8) + timedelta(minutes=30))
    assert horizon(almost, NOW, None)["weeks"] == 7.99


def test_a_weeks_argument_is_clamped_to_the_time_the_session_has_run():
    assert horizon(_row(), NOW, 4)["band_days"] == 28
    assert horizon(_row(), NOW, 26)["band_days"] == 70
    assert horizon(_row(), NOW, 4)["source"] == "weeks argument"


def test_g4_is_reached_only_when_both_minimums_are_met():
    g4 = {"min_weeks": 8, "min_trades": 20}
    assert g4_progress(g4, 8.0, 20)["reached"] is True
    assert g4_progress(g4, 7.99, 40)["reached"] is False
    assert g4_progress(g4, 30.0, 19)["reached"] is False


def test_a_g4_block_without_minimums_uses_the_defaults_and_says_so():
    progress = g4_progress({"paper_clean": True}, 0.0, 0)
    assert progress["reached"] is False
    assert (progress["min_weeks"], progress["min_trades"]) == (8, 20)
    assert len(progress["notes"]) == 2
    assert g4_progress({}, 9.0, 25)["reached"] is True


@pytest.mark.parametrize("bad", [None, "8 weeks", True])
def test_a_minimum_that_is_not_a_number_falls_back_to_the_default(bad):
    progress = g4_progress({"min_weeks": bad, "min_trades": 20}, 9.0, 25)
    assert progress["min_weeks"] == 8 and progress["reached"] is True


@pytest.mark.parametrize(
    ("weeks", "trades"), [(0, None), (-3, None), (500_000, None), (None, 0), (None, 10**9)]
)
def test_a_horizon_out_of_range_is_refused(weeks, trades):
    with pytest.raises(ToolFailure) as exc:
        check_horizon(weeks=weeks, trades=trades)
    assert exc.value.code == "invalid_request"


def test_a_horizon_in_range_or_absent_is_accepted():
    check_horizon(weeks=None, trades=None)
    check_horizon(weeks=8, trades=20)


def _trade(day: int, pnl: float) -> ClosedTrade:
    at = NOW - timedelta(days=30 - day)
    return ClosedTrade(entry_at=at, exit_at=at, ret=pnl / 1000, pnl=pnl, commission_frac=0.0001)


def test_paper_rates_are_those_of_the_newest_judged_trades_and_the_count_is_all():
    trades = [_trade(d, -5.0) for d in range(6)] + [_trade(d, 5.0) for d in range(6, 10)]
    capped = _paper_stats(trades, 4)
    assert (capped["trades"], capped["judged_trades"], capped["win_rate"]) == (10, 4, 1.0)
    whole = _paper_stats(trades, 10)
    assert (whole["judged_trades"], whole["win_rate"]) == (10, 0.4)


def test_a_closed_study_or_an_old_version_is_a_warning():
    study = SimpleNamespace(status="rejected", status_reason="curve fit", current_version=2)
    found = _standing_warnings(study, SimpleNamespace(version=1))
    assert "rejected: curve fit" in found[0] and "not the study's current version" in found[1]
    live = SimpleNamespace(status="tested", status_reason=None, current_version=1)
    assert _standing_warnings(live, SimpleNamespace(version=1)) == []


def _reading_trades(monkeypatch, *, run: int, paper: int):
    """A database stand-in: ``run`` trades behind the compare-to run, ``paper`` in the session."""
    stored = SimpleNamespace(id=1, end_date=NOW)
    db = SimpleNamespace(scalars=lambda _stmt: SimpleNamespace(first=lambda: stored))

    def closed(_db, *, run_pk=None, session_pk=None):
        count = run if run_pk is not None else paper
        return [_trade(d % 30, 5.0 if d % 2 else -5.0) for d in range(count)]

    monkeypatch.setattr(report, "closed_trades", closed)
    monkeypatch.setattr(report, "link_of", lambda _db, _run_id: None)
    return db


def test_a_band_is_fitted_to_the_run_only_when_asked(monkeypatch):
    db = _reading_trades(monkeypatch, run=30, paper=0)
    fitted = report.band_for_run(db, uuid4(), days=7, n=28, fit=True)["trade_windows"]
    assert (fitted["n"], fitted["count"]) == (11, 20)
    plain = report.band_for_run(db, uuid4(), days=7, n=28)["trade_windows"]
    assert (plain["n"], plain["count"]) == (28, 3)


def test_a_session_with_nearly_as_many_trades_as_the_run_is_still_judged(monkeypatch):
    db = _reading_trades(monkeypatch, run=30, paper=25)
    row = _row(
        id=7,
        name="s",
        session_id=uuid4(),
        spec={"strategies": [{"bar_types": ["QQQ.NASDAQ-1-DAY-LAST-EXTERNAL"]}]},
        linked_backtest_run_id=uuid4(),
        sealed_run_id=None,
        last_heartbeat_at=NOW,
        last_bar_at=NOW,
        runtime_flags=None,
    )
    out = report.session_report(db, row, g4={}, now=NOW, recent=0)
    assert out["band"]["trade_windows"]["count"] == 20
    assert (out["paper"]["trades"], out["paper"]["judged_trades"]) == (25, 11)
    assert out["comparison"]["win_rate"]["position"] != "n/a"
