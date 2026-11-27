"""Daily-bar stamp → session date (tasks.md 1b.1).

Two conventions are supported, one per series: a bar stamped at 00:00 New York carries
its session date (FirstRate ETF and futures daily bars), and a bar stamped at its CME
session open, 17:00 New York on the previous calendar day, belongs to the next day
(``D-1 17:00`` → ``D``; Sunday's open → Monday). Anything else is refused.
"""

from datetime import date

import pandas as pd
import pytest

from src.core.regimes.sessions import UnsupportedSessionStamp, session_dates


def utc(*stamps: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(list(stamps), tz="UTC")


def new_york(*stamps: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(list(stamps)).tz_localize("America/New_York").tz_convert("UTC")


def test_midnight_stamps_are_their_own_session_date():
    stamps = utc("2024-01-02 05:00", "2024-07-01 04:00")  # 00:00 EST, 00:00 EDT
    assert session_dates(stamps) == [date(2024, 1, 2), date(2024, 7, 1)]


def test_a_weekday_session_open_belongs_to_the_next_day():
    stamps = new_york("2024-01-02 17:00", "2024-01-03 17:00", "2024-01-04 17:00")
    assert session_dates(stamps) == [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def test_the_sunday_open_is_mondays_session():
    stamps = new_york("2024-01-04 17:00", "2024-01-07 17:00")  # Thursday, Sunday
    assert session_dates(stamps) == [date(2024, 1, 5), date(2024, 1, 8)]


def test_spring_forward_sunday_open_maps_to_monday():
    # 2024-03-10: clocks go forward at 02:00, so Sunday 17:00 is 21:00Z, not 22:00Z.
    stamps = utc("2024-03-07 22:00", "2024-03-10 21:00", "2024-03-11 21:00")
    assert session_dates(stamps) == [date(2024, 3, 8), date(2024, 3, 11), date(2024, 3, 12)]


def test_fall_back_sunday_open_maps_to_monday():
    # 2024-11-03: clocks go back at 02:00, so Sunday 17:00 is 22:00Z again.
    stamps = utc("2024-10-31 21:00", "2024-11-03 22:00", "2024-11-04 22:00")
    assert session_dates(stamps) == [date(2024, 11, 1), date(2024, 11, 4), date(2024, 11, 5)]


def test_the_wall_clock_hour_decides_not_the_utc_hour():
    # 22:00Z is 18:00 EDT in summer: not a 17:00 session open.
    with pytest.raises(UnsupportedSessionStamp, match="2024-07-01"):
        session_dates(utc("2024-07-01 22:00"))


def test_a_holiday_with_no_session_leaves_no_date():
    # Good Friday 2024-03-29: no CME session. Thursday's session opened Wednesday 17:00;
    # the next open is Sunday 17:00, for Monday.
    stamps = new_york("2024-03-27 17:00", "2024-03-31 17:00")
    assert session_dates(stamps) == [date(2024, 3, 28), date(2024, 4, 1)]


def test_christmas_midweek_skips_the_holiday():
    # Christmas 2024 is a Wednesday: Tuesday's session opened Monday 17:00, and the
    # next open is Wednesday 17:00, for Thursday.
    stamps = new_york("2024-12-23 17:00", "2024-12-25 17:00")
    assert session_dates(stamps) == [date(2024, 12, 24), date(2024, 12, 26)]


@pytest.mark.parametrize("stamp", ["2024-01-05 17:00", "2024-01-06 17:00"])
def test_no_session_opens_on_friday_or_saturday_evening(stamp):
    with pytest.raises(UnsupportedSessionStamp, match="no CME session opens"):
        session_dates(new_york(stamp))


@pytest.mark.parametrize("stamp", ["2024-01-02 09:30", "2024-01-02 16:00", "2024-01-02 00:01"])
def test_a_stamp_that_fits_no_convention_is_refused(stamp):
    with pytest.raises(UnsupportedSessionStamp, match="fits no supported"):
        session_dates(new_york(stamp))


def test_one_series_cannot_mix_conventions():
    stamps = new_york("2024-01-02 00:00", "2024-01-03 17:00")
    with pytest.raises(UnsupportedSessionStamp, match="mixes"):
        session_dates(stamps)


def test_naive_stamps_are_refused():
    with pytest.raises(UnsupportedSessionStamp, match="timezone"):
        session_dates(pd.DatetimeIndex(["2024-01-02"]))


def test_empty_series_has_no_sessions():
    assert session_dates(utc()) == []


@pytest.mark.parametrize("stamp", ["2024-01-06 00:00", "2024-01-07 00:00"])
def test_a_weekend_session_date_is_refused(stamp):
    with pytest.raises(UnsupportedSessionStamp, match="no session falls on a"):
        session_dates(new_york(stamp))
