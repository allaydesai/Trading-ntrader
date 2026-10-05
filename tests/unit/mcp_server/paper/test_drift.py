"""Drift: a paper session against its band, with the likely cause from stored facts (S7.2, S7.3)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.mcp_server.paper.drift import (
    SessionHealth,
    bar_seconds,
    classify,
    commission_flag,
    compare,
    health_flags,
    position,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
BAND = {
    "time_windows": {"trades": {"p5": 2.0, "p50": 4.0, "p95": 7.0}},
    "trade_windows": {
        "win_rate": {"p5": 0.4, "p50": 0.6, "p95": 0.8},
        "avg_trade_return": {"p5": -0.002, "p50": 0.004, "p95": 0.01},
        "trade_drawdown": {"p5": -0.06, "p50": -0.02, "p95": -0.005},
    },
}


def _paper(**overrides):
    stats = {"trades": 6, "win_rate": 0.6, "avg_trade_return": 0.004, "trade_drawdown": -0.02}
    return {**stats, **overrides}


def _health(**overrides) -> SessionHealth:
    fields = dict(
        status="running",
        last_started_at=NOW - timedelta(days=20),
        last_bar_at=NOW - timedelta(hours=20),
        last_heartbeat_at=NOW - timedelta(seconds=10),
        runtime_flags=None,
        bar_seconds=86_400.0,
    )
    return SessionHealth(**{**fields, **overrides})


def _kinds(flags):
    return {f["kind"] for f in flags}


@pytest.mark.parametrize(
    ("bar_type", "seconds"),
    [
        ("QQQ.NASDAQ-1-DAY-LAST-EXTERNAL", 86_400.0),
        ("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL", 300.0),
        ("AAPL.NASDAQ-1-HOUR-LAST-EXTERNAL", 3_600.0),
        ("not a bar type", None),
    ],
)
def test_bar_seconds_reads_the_bar_interval(bar_type, seconds):
    assert bar_seconds(bar_type) == seconds


def test_position_is_inside_on_the_edges_and_na_without_a_value_or_band():
    band = {"p5": 1.0, "p50": 2.0, "p95": 3.0}
    assert position(1.0, band) == "inside" and position(3.0, band) == "inside"
    assert position(0.5, band) == "below" and position(3.5, band) == "above"
    assert position(None, band) == "n/a" and position(1.0, None) == "n/a"


def test_trade_count_is_judged_on_time_and_the_rest_on_trades():
    rows = compare(_paper(trades=1, win_rate=0.9), BAND)
    assert rows["trades"]["position"] == "below"
    assert rows["trades"]["band"] == BAND["time_windows"]["trades"]
    assert rows["win_rate"]["position"] == "above"
    assert rows["avg_trade_return"]["position"] == "inside"


def test_a_session_inside_its_band_with_clean_health_is_inside():
    drift = classify(compare(_paper(), BAND), health_flags(_health(), NOW), trades=6)
    assert drift["status"] == "inside_band"
    assert drift["likely_cause"] == "none" and drift["flags"] == []
    assert drift["slippage"]["measurable"] is False


def test_results_outside_with_clean_execution_point_at_strategy_or_regime():
    drift = classify(compare(_paper(win_rate=0.2), BAND), [], trades=6)
    assert drift["status"] == "flagged"
    assert _kinds(drift["flags"]) == {"results_outside_band"}
    assert drift["likely_cause"] == "strategy_or_regime"
    assert "win_rate below" in drift["flags"][0]["detail"]


def test_a_shallower_drawdown_than_the_band_is_not_drift():
    drift = classify(compare(_paper(trade_drawdown=-0.001), BAND), [], trades=6)
    assert drift["flags"] == []


def test_too_few_trades_are_too_early_to_judge_results_but_not_the_count():
    early = classify(compare(_paper(trades=3, win_rate=0.0), BAND), [], trades=3)
    assert early["status"] == "too_early" and early["flags"] == []
    none = classify(compare(_paper(trades=0, win_rate=None), BAND), [], trades=0)
    assert _kinds(none["flags"]) == {"fewer_trades"}
    assert none["likely_cause"] == "signals"


def test_execution_problems_explain_results_outside_the_band():
    flags = health_flags(
        _health(runtime_flags={"v": 1, "order_rejections": {"rejected": 2, "denied": 1}}), NOW
    )
    drift = classify(compare(_paper(win_rate=0.2), BAND), flags, trades=6)
    assert _kinds(drift["flags"]) == {"order_rejections", "results_outside_band"}
    assert drift["likely_cause"] == "execution"
    assert "3" in next(f for f in drift["flags"] if f["kind"] == "order_rejections")["detail"]


def test_two_kinds_of_problem_are_mixed():
    flags = health_flags(_health(runtime_flags={"failed_strategies": [{"strategy_id": "x"}]}), NOW)
    flags.append({"kind": "spec_mismatch", "cause": "config", "detail": "trade_size"})
    drift = classify(compare(_paper(), BAND), flags, trades=6)
    assert drift["likely_cause"] == "mixed"


def test_a_running_session_with_old_bars_or_heartbeat_is_flagged():
    stale = health_flags(_health(last_bar_at=NOW - timedelta(days=6)), NOW)
    assert _kinds(stale) == {"stale_bars"}
    assert stale[0]["cause"] == "signals"
    dead = health_flags(_health(last_heartbeat_at=NOW - timedelta(minutes=10)), NOW)
    assert _kinds(dead) == {"not_alive"}
    minute = _health(bar_seconds=60.0, last_bar_at=NOW - timedelta(days=3))
    assert health_flags(minute, NOW) == []  # a weekend without bars is normal


def test_a_stopped_session_is_not_stale_or_dead():
    stopped = _health(
        status="stopped", last_bar_at=NOW - timedelta(days=30), last_heartbeat_at=None
    )
    assert health_flags(stopped, NOW) == []


def test_malformed_runtime_flags_are_read_tolerantly():
    odd = health_flags(
        _health(runtime_flags={"order_rejections": {"rejected": "x"}, "failed_strategies": 3}),
        NOW,
    )
    assert odd == []
    assert health_flags(_health(runtime_flags=["not", "a", "dict"]), NOW) == []
    lost = health_flags(_health(runtime_flags={"connection_lost_at": "2026-10-04T10:00Z"}), NOW)
    assert _kinds(lost) == {"connection_lost"}
    assert lost[0]["cause"] == "execution"


def test_commission_is_flagged_only_when_well_above_the_backtest():
    assert commission_flag(0.0005, 0.0002)["kind"] == "commission_high"
    assert commission_flag(0.0003, 0.0002) is None
    assert commission_flag(0.00005, 0.00001) is None  # under one basis point
    assert commission_flag(None, 0.0002) is None


def test_a_holiday_weekend_without_a_daily_bar_is_not_stale():
    # Friday's bar, Monday a holiday, read late on Tuesday before the bar lands.
    quiet = _health(last_bar_at=NOW - timedelta(days=4, hours=6))
    assert health_flags(quiet, NOW) == []


def test_a_value_on_the_rounded_band_edge_is_inside():
    third = {"p5": round(1 / 3, 6), "p50": round(1 / 3, 6), "p95": round(1 / 3, 6)}
    assert position(1 / 3, third) == "inside"
    two_thirds = {"p5": round(2 / 3, 6), "p50": round(2 / 3, 6), "p95": round(2 / 3, 6)}
    assert position(2 / 3, two_thirds) == "inside"
