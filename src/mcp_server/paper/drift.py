"""Drift: a paper session against its expectation band, with a likely cause (S7.2, S7.3).

Pure; ``now`` is always passed in. The causes come only from what NTrader
stores, because a session records neither the price a signal was decided at
nor its orders:

- **signals** (missed or extra): the trade count outside its band, bars that
  stopped arriving, a session marked running whose heartbeat stopped, a
  strategy the node contained after it raised;
- **execution** (costs and refusals): order rejections, commission per trade
  well above the backtest's, and a lost broker connection once NTrader records
  one (``connection_lost_at`` has a reader here and no writer yet);
- **config**: the session's stored spec differs from the frozen candidate;
- **strategy or regime**: win rate, average trade or drawdown outside the band
  while nothing above explains it.

Slippage and fill quality cannot be measured: trades store only average fill
prices. A result *above* the band is still flagged here (too good can be a
config error); only the scorecard's G4 treats it as not a failure.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.models.session import DEFAULT_HEARTBEAT_INTERVAL_SECONDS

#: Below this many closed trades, win rate and average trade say nothing yet.
MIN_TRADES_TO_JUDGE = 5
#: Bars may pause this long before a running session is stale: a holiday weekend
#: is four days from one daily bar to the next, plus the wait for that bar.
STALE_FLOOR = timedelta(days=5)
STALE_BARS = 3
DEAD_HEARTBEATS = 3
#: Paper commission per notional must exceed the backtest's by this factor, and one bp.
COMMISSION_FACTOR = 2.0
COMMISSION_FLOOR = 0.0001
SLIPPAGE = {
    "measurable": False,
    "reason": "Trades store only average fill prices; no signal or arrival price is "
    "recorded, so slippage against the decision price cannot be computed.",
}
_SECONDS = {"SECOND": 1, "MINUTE": 60, "HOUR": 3_600, "DAY": 86_400, "WEEK": 604_800}
_BAR = re.compile(r"-(\d+)-(SECOND|MINUTE|HOUR|DAY|WEEK)-")
_TRADE_AXIS = ("win_rate", "avg_trade_return", "trade_drawdown")
#: Directions in which a result is drift: a shallower drawdown than the band is not.
_ADVERSE = {
    "win_rate": {"below", "above"},
    "avg_trade_return": {"below", "above"},
    "trade_drawdown": {"below"},
}


@dataclass(frozen=True)
class SessionHealth:
    """What a session row says about whether it is running as it should."""

    status: str
    last_started_at: datetime | None
    last_bar_at: datetime | None
    last_heartbeat_at: datetime | None
    runtime_flags: Any
    bar_seconds: float | None


def bar_seconds(bar_type: str) -> float | None:
    """The interval of a bar type such as ``QQQ.NASDAQ-1-DAY-LAST-EXTERNAL``, in seconds."""
    match = _BAR.search(bar_type.upper())
    return None if match is None else float(int(match[1]) * _SECONDS[match[2]])


def position(value: float | None, band: dict[str, float] | None) -> str:
    """``below``, ``inside`` or ``above`` the 5th-95th percentile range, or ``n/a``."""
    if value is None or band is None:
        return "n/a"
    value = round(value, 6)  # the band's own precision: a value on an edge is inside
    if value < band["p5"]:
        return "below"
    return "above" if value > band["p95"] else "inside"


def compare(paper: dict[str, Any], band: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each paper statistic beside its band: trade count on time, the rest on trades."""
    bands = {"trades": (band.get("time_windows") or {}).get("trades")}
    bands |= {key: (band.get("trade_windows") or {}).get(key) for key in _TRADE_AXIS}
    return {
        key: {"paper": paper.get(key), "band": bands[key], "position": position(paper.get(key), b)}
        for key, b in bands.items()
    }


def _flag(kind: str, cause: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "cause": cause, "detail": detail}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _flags_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _runtime_flags(raw: Any) -> list[dict[str, str]]:
    """Flags from ``runtime_flags``, which cover only the current run since its last start."""
    flags = _flags_dict(raw)
    found = []
    rejections = _flags_dict(flags.get("order_rejections"))
    refused = _count(rejections.get("rejected")) + _count(rejections.get("denied"))
    if refused:
        last = _flags_dict(rejections.get("last"))
        reason = f" (last: {last['reason']})" if last.get("reason") else ""
        found.append(
            _flag("order_rejections", "execution", f"{refused} orders refused since start{reason}")
        )
    failed = flags.get("failed_strategies")
    if (isinstance(failed, list) and failed) or flags.get("all_failed") is True:
        found.append(
            _flag("strategy_failed", "signals", "A strategy raised and was stopped by the node")
        )
    if flags.get("connection_lost_at"):
        detail = f"Broker connection lost at {flags['connection_lost_at']}"
        found.append(_flag("connection_lost", "execution", detail))
    return found


def health_flags(health: SessionHealth, now: datetime) -> list[dict[str, str]]:
    """Signal and execution flags from the session row alone."""
    found = _runtime_flags(health.runtime_flags)
    if health.status != "running":
        return found
    heartbeat_limit = timedelta(seconds=DEAD_HEARTBEATS * DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
    beat = health.last_heartbeat_at
    if beat is None or now - beat > heartbeat_limit:
        age = "never" if beat is None else f"{(now - beat).total_seconds() / 60:.0f} min ago"
        detail = f"Marked running, but the last heartbeat was {age}: the node may be down"
        found.append(_flag("not_alive", "signals", detail))
    seen = health.last_bar_at or health.last_started_at
    limit = max(STALE_FLOOR, timedelta(seconds=STALE_BARS * (health.bar_seconds or 0)))
    if seen is not None and now - seen > limit:
        days = (now - seen).total_seconds() / 86_400
        found.append(_flag("stale_bars", "signals", f"No bar for {days:.1f} days"))
    return found


def commission_flag(paper: float | None, backtest: float | None) -> dict[str, str] | None:
    """A flag when the median paper commission per notional is well above the backtest's."""
    if paper is None or backtest is None:
        return None
    if paper > COMMISSION_FACTOR * backtest and paper > COMMISSION_FLOOR:
        detail = f"Median commission {paper * 1e4:.1f} bp of notional vs {backtest * 1e4:.1f} bp"
        return _flag("commission_high", "execution", detail)
    return None


def _result_flags(rows: dict[str, dict[str, Any]], trades: int) -> list[dict[str, str]]:
    found = []
    count = rows["trades"]["position"]
    if count in ("below", "above"):
        kind = "fewer_trades" if count == "below" else "extra_trades"
        detail = f"{rows['trades']['paper']} trades vs a band of {rows['trades']['band']}"
        found.append(_flag(kind, "signals", detail))
    if trades < MIN_TRADES_TO_JUDGE:
        return found
    outside = [
        f"{key} {rows[key]['position']}"
        for key in _TRADE_AXIS
        if rows[key]["position"] in _ADVERSE[key]
    ]
    if outside:
        found.append(_flag("results_outside_band", "strategy_or_regime", ", ".join(outside)))
    return found


def classify(
    rows: dict[str, dict[str, Any]], flags: list[dict[str, str]], *, trades: int
) -> dict[str, Any]:
    """Every flag, the status and the likely cause: other causes explain results first."""
    every = flags + _result_flags(rows, trades)
    causes = {f["cause"] for f in every if f["cause"] != "strategy_or_regime"}
    if len(causes) > 1:
        likely = "mixed"
    elif causes:
        likely = causes.pop()
    else:
        likely = "strategy_or_regime" if every else "none"
    if every:
        status = "flagged"
    else:
        status = "too_early" if trades < MIN_TRADES_TO_JUDGE else "inside_band"
    return {"status": status, "flags": every, "likely_cause": likely, "slippage": SLIPPAGE}
