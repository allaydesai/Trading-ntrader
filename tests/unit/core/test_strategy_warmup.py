"""Story 4.4 — the history window a strategy asks for at start (D-H).

``warmup_lookback`` is pure and framework-free: it duck-types the bar type
(``spec.is_time_aggregated()``, ``spec.timedelta``) so a strategy can call it
without this module importing Nautilus. Most tests below therefore use a
two-attribute stand-in; the real-``BarType`` and IB-duration checks import
Nautilus *inside* the test body, the ``test_live_session_phases.py`` precedent.

The load-bearing facts it encodes, measured against ``nautilus-trader
1.220.0`` (story Measured facts F3):

- The IB adapter turns ``end - start`` into a duration string with
  ``timedelta_to_duration_str``: under one day it is **seconds**, and a
  seconds duration ending before the open (``useRTH=1``) covers no bar at all,
  so the adapter answers with nothing and the strategy's history callback never
  fires. A ``>= 1 minute`` strategy must therefore always ask in whole days.
- Weeks, months and years are formatted with ``:.0f`` — which **rounds**, so 74
  days would be sent as ``"2 M"`` (60 days, short). The window is snapped up to
  a whole multiple of the unit the adapter will actually send.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.core.strategy_warmup import (
    NON_TIME_BAR_LOOKBACK,
    RTH_SESSION,
    WEEKEND_AND_HOLIDAY_SLACK_DAYS,
    warmup_lookback,
)

pytestmark = pytest.mark.unit


class _Spec:
    """The two ``BarSpecification`` members the helper reads, and nothing else.

    ``timedelta`` raises for a non-time aggregation exactly as the real
    property does (``ValueError: Aggregation not time based``), so a helper that
    read it before checking ``is_time_aggregated()`` fails here too.
    """

    def __init__(self, step: timedelta | None) -> None:
        self._step = step

    def is_time_aggregated(self) -> bool:
        return self._step is not None

    @property
    def timedelta(self) -> timedelta:
        if self._step is None:
            raise ValueError("Aggregation not time based")
        return self._step


def _bar_type(step: timedelta | None) -> SimpleNamespace:
    """A duck-typed stand-in for ``nautilus_trader.model.data.BarType``."""
    return SimpleNamespace(spec=_Spec(step))


def _sent_days(duration: str) -> int:
    """Calendar days an IB duration string asks for (``"5 D"`` → 5)."""
    amount, unit = duration.split()
    return int(amount) * {"D": 1, "W": 7, "M": 30, "Y": 365}[unit]


class TestIntradayBarsAskInWholeDays:
    def test_one_minute_bars_for_a_twenty_bar_window_ask_for_five_days(self):
        """One RTH session holds 390 one-minute bars; 20 need one session,
        which is two calendar days at the 7/5 weekend ratio, plus the slack."""
        lookback = warmup_lookback(_bar_type(timedelta(minutes=1)), 20)

        assert lookback == timedelta(days=2 + WEEKEND_AND_HOLIDAY_SLACK_DAYS)

    def test_one_minute_bars_never_ask_for_less_than_a_day(self):
        """F3: a seconds duration ending pre-open returns nothing at all."""
        for bars_needed in (1, 2, 20, 50, 389, 390):
            lookback = warmup_lookback(_bar_type(timedelta(minutes=1)), bars_needed)

            assert lookback >= timedelta(days=1), bars_needed
            assert lookback.seconds == 0 and lookback.microseconds == 0, bars_needed

    def test_a_window_longer_than_one_session_asks_for_more_sessions(self):
        one_session = warmup_lookback(_bar_type(timedelta(minutes=1)), 390)
        two_sessions = warmup_lookback(_bar_type(timedelta(minutes=1)), 391)

        assert two_sessions > one_session

    def test_hourly_bars_count_six_per_session(self):
        """6h30m // 1h = 6 bars a session, so 20 bars need 4 sessions."""
        assert RTH_SESSION // timedelta(hours=1) == 6

        lookback = warmup_lookback(_bar_type(timedelta(hours=1)), 20)

        # ceil(4 * 7 / 5) + 3 = 9 days, snapped up to whole weeks.
        assert lookback == timedelta(days=14)


class TestDailyBars:
    def test_fifty_daily_bars_snap_up_to_whole_months(self):
        """70 trading-calendar days + 3 slack = 73, which the adapter would
        send as ``"2 M"`` (60) — short. Snapped up to 90 instead."""
        lookback = warmup_lookback(_bar_type(timedelta(days=1)), 50)

        assert lookback == timedelta(days=90)

    def test_a_year_or_more_snaps_to_whole_years(self):
        lookback = warmup_lookback(_bar_type(timedelta(days=1)), 300)

        assert lookback.days % 365 == 0
        assert lookback.days >= 300 * 7 / 5


class TestSubMinuteBars:
    def test_sub_minute_bars_ask_in_seconds(self):
        """IB's small-bar rules apply; a days-long 5-second request is refused.
        Pre-open this returns no data and the runner contains the strategy —
        disclosed in the module docstring, not solved here."""
        lookback = warmup_lookback(_bar_type(timedelta(seconds=5)), 20)

        assert lookback == timedelta(seconds=5 * 20 * 3)

    def test_the_seconds_window_is_never_below_the_adapters_minimum(self):
        assert warmup_lookback(_bar_type(timedelta(seconds=1)), 2) == timedelta(seconds=30)

    def test_a_sub_minute_window_of_a_day_or_more_is_whole_days(self):
        """30-second bars at period 1000 ask 90,000 s; the adapter would send
        that as ``"1 D"`` (86,400 s) — short. Code review 2026-09-22."""
        lookback = warmup_lookback(_bar_type(timedelta(seconds=30)), 1000)

        assert lookback == timedelta(days=2)

    def test_the_day_floor_is_ignored_for_sub_minute_bars(self):
        """IB's small-bar rules refuse a days-long request, so ``momentum``'s
        ``warmup_days`` cannot apply to them (code review 2026-09-22)."""
        lookback = warmup_lookback(_bar_type(timedelta(seconds=5)), 20, minimum=timedelta(days=1))

        assert lookback == timedelta(seconds=300)


class TestTheFloor:
    """``minimum`` — ``momentum``'s ``warmup_days`` — is snapped like the rest."""

    def test_a_floor_below_the_computed_window_changes_nothing(self):
        bar_type = _bar_type(timedelta(minutes=1))

        assert warmup_lookback(bar_type, 20, minimum=timedelta(days=1)) == warmup_lookback(
            bar_type, 20
        )

    def test_a_floor_above_it_is_snapped_up_so_the_adapter_cannot_cut_it_short(self):
        """10 days would be sent as ``"1 W"`` (7) — so it becomes 14."""
        lookback = warmup_lookback(_bar_type(timedelta(minutes=1)), 20, minimum=timedelta(days=10))

        assert lookback == timedelta(days=14)


class _CalendarSpec:
    """A MONTH/YEAR spec: time-aggregated, but ``timedelta`` raises for it."""

    def __init__(self, text: str, step: int = 1) -> None:
        self._text = text
        self.step = step

    def is_time_aggregated(self) -> bool:
        return True

    @property
    def timedelta(self) -> timedelta:
        raise ValueError("no fixed interval")

    def __str__(self) -> str:
        return self._text


class TestCalendarBars:
    """MONTH/YEAR bars have no fixed interval — ``spec.timedelta`` raises —
    which used to abort ``on_start``, even in a backtest (code review 2026-09-22)."""

    def test_month_bars_ask_thirty_one_days_a_bar(self):
        lookback = warmup_lookback(SimpleNamespace(spec=_CalendarSpec("1-MONTH-LAST")), 20)

        assert lookback.days >= 20 * 31
        assert lookback.days % 365 == 0

    def test_year_bars_ask_a_leap_year_a_bar(self):
        lookback = warmup_lookback(SimpleNamespace(spec=_CalendarSpec("1-YEAR-LAST")), 3)

        assert lookback.days >= 3 * 366

    def test_the_real_month_bar_type_does_not_raise(self):
        from nautilus_trader.model.data import BarType

        lookback = warmup_lookback(BarType.from_str("AAPL.NASDAQ-1-MONTH-LAST-EXTERNAL"), 20)

        assert lookback.days >= 20 * 31


class TestNonTimeBars:
    def test_a_non_time_bar_type_gets_the_fixed_default(self):
        """Backtest-only: a live spec refuses non-time bars at config time
        (``resolve_live_bar_types``), and a backtest answers the request with
        nothing anyway. The strategy still takes its one subscribe path."""
        assert warmup_lookback(_bar_type(None), 20) == NON_TIME_BAR_LOOKBACK


class TestRefusals:
    @pytest.mark.parametrize("bars_needed", [0, -1])
    def test_a_non_positive_window_is_refused(self, bars_needed):
        with pytest.raises(ValueError, match="bars_needed"):
            warmup_lookback(_bar_type(timedelta(minutes=1)), bars_needed)

    def test_a_non_positive_step_is_refused(self):
        with pytest.raises(ValueError, match="step"):
            warmup_lookback(_bar_type(timedelta(0)), 20)


class TestAgainstTheRealAdapterMapping:
    """The two external facts, checked against the installed wheel itself."""

    @pytest.mark.parametrize(
        ("bar_type", "bars_needed"),
        [
            ("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL", 20),
            ("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL", 50),
            ("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL", 200),
            ("AAPL.NASDAQ-1-HOUR-LAST-EXTERNAL", 20),
            ("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL", 20),
            ("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL", 50),
            ("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL", 200),
            ("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL", 300),
        ],
    )
    def test_the_duration_the_adapter_sends_is_never_shorter_than_asked(
        self, bar_type, bars_needed
    ):
        from nautilus_trader.adapters.interactive_brokers.parsing.data import (
            timedelta_to_duration_str,
        )
        from nautilus_trader.model.data import BarType

        lookback = warmup_lookback(BarType.from_str(bar_type), bars_needed)
        # The adapter formats `end - start`, which is the lookback plus the
        # microseconds between the strategy's call and the engine's `now`.
        sent = timedelta_to_duration_str(lookback + timedelta(microseconds=250))

        assert not sent.endswith(" S"), f"{bar_type} would be sent in seconds: {sent}"
        assert _sent_days(sent) >= lookback.days, f"{bar_type}/{bars_needed} sent as {sent}"

    def test_the_real_bar_type_duck_types(self):
        from nautilus_trader.model.data import BarType

        real = warmup_lookback(BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"), 20)

        assert real == warmup_lookback(_bar_type(timedelta(minutes=1)), 20)
