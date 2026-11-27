"""The regime calendar's session date: a daily bar's New York midnight stamp (no DB)."""

from datetime import date

import pandas as pd
import pytest

from src.mcp_server.analysis import regime_calendar

pytestmark = pytest.mark.component


def _stamped(hour_utc: int) -> pd.DataFrame:
    times = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], tz="UTC") + pd.Timedelta(hours=hour_utc)
    return pd.DataFrame(
        {"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0],
         "volume": [0.0, 0.0]},
        index=times,
    )  # fmt: skip


def test_session_date_is_the_new_york_midnight_stamp(monkeypatch):
    monkeypatch.setattr(regime_calendar, "read_bars", lambda *a: _stamped(5))  # 00:00 EST
    frame = regime_calendar.load_reference_bars("firstrate-etf", "SPY")
    assert list(frame.index) == [date(2024, 1, 2), date(2024, 1, 3)]


def test_bars_stamped_at_the_futures_open_are_refused(monkeypatch):
    monkeypatch.setattr(regime_calendar, "read_bars", lambda *a: _stamped(22))  # 17:00 EST
    with pytest.raises(regime_calendar.ToolFailure) as caught:
        regime_calendar.load_reference_bars("firstrate-futures", "ES")
    assert caught.value.code == "unsupported_session_stamp"
