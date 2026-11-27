"""Map a daily bar's timestamp to its trading-session date (tasks.md 1b.1).

The calendar is keyed by session date, so every series must say which session each of
its daily bars is. Two stamping conventions are supported, and one series must use one:

- ``session_date``: 00:00 New York of the session date. FirstRate ETF and futures daily
  bars (``firstrate-etf``, ``firstrate-futures``) are stamped this way.
- ``session_open``: 17:00 New York on the previous calendar day (``D-1 17:00`` → ``D``;
  a Sunday stamp → Monday). This is the importers' session-stamp convention, the end of the
  previous trading day; CME Globex itself opens at 18:00 New York after the maintenance
  break, and an 18:00 stamp is refused because no importer writes it. No session follows a
  Friday or Saturday 17:00, and no session date is a Saturday or Sunday.

The hour is read on the New York wall clock, so the UTC hour moves with daylight saving
and a 17:00 stamp is 21:00Z in summer and 22:00Z in winter. An exchange holiday has no
bar, so it simply yields no date. Any other stamp is refused rather than guessed: keying
labels one day off would silently misalign every report built on them. (FX trade days use
their own rule, ``src.services.fx.sessions``, which keeps out-of-session bars; FX has no
regime calendar.)
"""

from datetime import date, timedelta

import pandas as pd

NEW_YORK = "America/New_York"
#: (hour, minute) on the New York wall clock → convention name.
CONVENTIONS: dict[tuple[int, int], str] = {(0, 0): "session_date", (17, 0): "session_open"}
#: Friday and Saturday (pandas: Monday = 0): the CME is shut from Friday 17:00 to Sunday 17:00.
_NO_OPEN_DAYS = (4, 5)
#: Saturday and Sunday: no US equity or CME session is dated on a weekend.
_WEEKEND = (5, 6)


class UnsupportedSessionStamp(ValueError):
    """A daily-bar stamp that fits no supported convention."""


def session_convention(stamps: pd.DatetimeIndex) -> str | None:
    """The single convention of ``stamps``; ``None`` when empty. Raises on anything else."""
    if stamps.tz is None:
        raise UnsupportedSessionStamp("daily-bar stamps carry no timezone")
    local = stamps.tz_convert(NEW_YORK)
    found: set[str] = set()
    for stamp in local:
        convention = CONVENTIONS.get((stamp.hour, stamp.minute))
        if convention is None or stamp.second or stamp.microsecond or stamp.nanosecond:
            raise UnsupportedSessionStamp(
                f"{stamp.isoformat()} fits no supported daily-bar stamp "
                "(00:00 New York session date, or 17:00 New York the day before the session)"
            )
        if convention == "session_open" and stamp.dayofweek in _NO_OPEN_DAYS:
            raise UnsupportedSessionStamp(
                f"{stamp.isoformat()}: no CME session opens on a {stamp.day_name()} evening"
            )
        if convention == "session_date" and stamp.dayofweek in _WEEKEND:
            raise UnsupportedSessionStamp(
                f"{stamp.isoformat()}: no session falls on a {stamp.day_name()}"
            )
        found.add(convention)
    if len(found) > 1:
        raise UnsupportedSessionStamp(f"the series mixes stamp conventions: {sorted(found)}")
    return found.pop() if found else None


def session_dates(stamps: pd.DatetimeIndex) -> list[date]:
    """The session date of every stamp, in order."""
    convention = session_convention(stamps)
    local_dates = list(stamps.tz_convert(NEW_YORK).date) if len(stamps) else []
    if convention == "session_open":
        return [day + timedelta(days=1) for day in local_dates]
    return local_dates
