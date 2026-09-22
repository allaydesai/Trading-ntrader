"""How far back a strategy asks for history before it subscribes (Story 4.4, D-H).

Owns: :func:`warmup_lookback` — the calendar span a strategy passes to
``request_bars(start=now - lookback)`` so that, once the history is loaded,
every one of its indicators is already initialised at the first live bar.

Does not own: the request itself, or the choreography around it — each
strategy calls ``register_indicator_for_bars`` / ``request_bars`` /
``subscribe_bars`` inline in its own ``on_start`` and history callback (AR40:
the three calls stay visible in the strategy file) — nor anything the runner
does about a request that never answers (``src/core/live_session_warmup.py``).

**Framework-free by contract, and deliberately not a ``live_*`` module.** A
strategy imports this in both engines; a strategy importing a ``live_*`` name
would read as live-coupling, and ``LIVE_MODULE_GLOBS`` would start scanning a
module that has nothing live about it. The bar type is duck-typed —
``bar_type.spec.is_time_aggregated()`` and ``bar_type.spec.timedelta`` — so
nothing here imports ``nautilus_trader``.

**Why whole IB units, measured against ``nautilus-trader 1.220.0``** (story
Measured facts F2/F3). The IB adapter sends ``end - start`` through
``timedelta_to_duration_str`` (``adapters/interactive_brokers/parsing/data.py:
91-101``):

- under one day it becomes **seconds**, and a seconds window ending before the
  open covers no RTH bar — the adapter then answers with nothing, and a
  strategy whose ``subscribe_bars`` lives in its history callback never
  subscribes. So ``>= 1 minute`` bars always ask in whole days, which is also
  what keeps a session started five minutes before the open warm (NFR3);
- weeks, months and years are formatted with ``:.0f``, which **rounds** — 74
  days would be sent as ``"2 M"`` (60 days, short). So the span is snapped up to
  a whole multiple of the unit the adapter will actually send.

Known, accepted limits: the RTH session is taken as 6h30m every trading day,
and a half-day holds fewer bars than that — the weekend-and-holiday slack is
what covers the shortfall, so a window spanning a half-day and a long weekend
at once is the tightest case; multi-day and calendar (month/year) steps
overshoot, which costs a longer request and nothing else; and sub-minute bars
ask in seconds, which **pre-open returns no data** — the runner then contains
the strategy rather than letting it sit silent (story D-D).
"""

import math
from datetime import timedelta
from typing import Any

#: One regular US equity session. Only ever used to count how many bars of a
#: given step fit in a trading day; nothing here knows about time zones.
RTH_SESSION = timedelta(hours=6, minutes=30)

#: Calendar days added on top of the 7/5 weekend ratio: a long weekend plus a
#: holiday. Chosen to over-ask — a longer history costs one slower request; a
#: shorter one leaves the indicators cold at the first live bar.
WEEKEND_AND_HOLIDAY_SLACK_DAYS = 3

#: Sub-minute windows ask for this many times the bars they need, because a
#: seconds window is calendar time and the market may have been quiet in it.
SUB_MINUTE_HISTORY_MULTIPLIER = 3

#: The adapter never sends fewer than 30 seconds (``max(30, seconds)``).
MINIMUM_SECONDS_WINDOW = timedelta(seconds=30)

#: A non-time aggregation (tick/volume/value bars) is backtest-only — a live
#: spec refuses it at config time (``resolve_live_bar_types``) — and a backtest
#: answers every history request with nothing. The strategy still takes its one
#: subscribe path; the span it asks for is irrelevant, so it is fixed.
NON_TIME_BAR_LOOKBACK = timedelta(days=5)

#: Calendar days per step for the aggregations with no fixed interval
#: (``BarSpecification.timedelta`` raises for MONTH and YEAR), rounded up so a
#: window of them never falls short.
_CALENDAR_STEP_DAYS = {"MONTH": 31, "YEAR": 366}

#: The units the adapter formats a whole-day span into, largest first, each in
#: days. Snapping up to a whole multiple is what makes ``:.0f`` exact.
_IB_DAY_UNITS = (365, 30, 7)


def warmup_lookback(
    bar_type: Any, bars_needed: int, *, minimum: timedelta = timedelta(0)
) -> timedelta:
    """Return the history span that yields at least ``bars_needed`` bars.

    Args:
        bar_type: The strategy's bar type — anything with ``spec.
            is_time_aggregated()``, ``spec.timedelta`` and ``spec.step`` (a
            Nautilus ``BarType`` in production). Duck-typed so this module
            never imports Nautilus.
        bars_needed: The longest indicator period the strategy registers.
        minimum: A floor under the computed span (``momentum``'s
            ``warmup_days``), applied **before** snapping so the adapter's
            rounding cannot cut it short. Ignored for sub-minute bars: IB's
            small-bar rules refuse a days-long request for them.

    Returns:
        A span to subtract from the clock's ``now``. Whole days for any step of
        one minute or more, and for any sub-minute window of a day or more.

    Raises:
        ValueError: ``bars_needed`` is below 1, or the bar step is not positive.
    """
    if bars_needed < 1:
        raise ValueError(f"bars_needed must be at least 1, got {bars_needed}")
    spec = bar_type.spec
    if not spec.is_time_aggregated():
        return NON_TIME_BAR_LOOKBACK
    step = _fixed_step(spec)
    if step is None:
        calendar_days = bars_needed * _calendar_step_days(spec)
    elif step <= timedelta(0):
        raise ValueError(f"bar step must be positive, got {step}")
    elif step < timedelta(minutes=1):
        window = max(MINIMUM_SECONDS_WINDOW, step * bars_needed * SUB_MINUTE_HISTORY_MULTIPLIER)
        if window < timedelta(days=1):
            return window
        # The adapter formats a day or more as whole days, rounding down.
        return timedelta(days=_snap_up_to_ib_unit(math.ceil(window / timedelta(days=1))))
    elif step < timedelta(days=1):
        sessions = math.ceil(bars_needed / max(1, RTH_SESSION // step))
        calendar_days = math.ceil(sessions * 7 / 5)
    else:
        sessions = bars_needed * math.ceil(step / timedelta(days=1))
        calendar_days = math.ceil(sessions * 7 / 5)
    calendar_days = max(calendar_days + WEEKEND_AND_HOLIDAY_SLACK_DAYS, _whole_days(minimum))
    return timedelta(days=_snap_up_to_ib_unit(calendar_days))


def _fixed_step(spec: Any) -> timedelta | None:
    """The bar step, or ``None`` for an aggregation with no fixed interval."""
    try:
        step: timedelta = spec.timedelta
    except ValueError:  # MONTH/YEAR: `get_interval_ns` has no fixed length for them
        return None
    return step


def _calendar_step_days(spec: Any) -> int:
    """Calendar days one MONTH or YEAR bar spans, rounded up (default: a year)."""
    per_unit = next((d for name, d in _CALENDAR_STEP_DAYS.items() if name in str(spec)), 366)
    return int(getattr(spec, "step", 1)) * per_unit


def _whole_days(span: timedelta) -> int:
    return math.ceil(span / timedelta(days=1)) if span > timedelta(0) else 0


def _snap_up_to_ib_unit(days: int) -> int:
    """The smallest whole multiple of the adapter's unit for ``days``, ``>= days``."""
    for unit in _IB_DAY_UNITS:
        if days >= unit:
            return math.ceil(days / unit) * unit
    return days
