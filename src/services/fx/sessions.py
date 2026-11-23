"""The FX trading day: the New York 17:00 cutoff (design section 9, decision D5).

The spot FX week runs Sunday 17:00 to Friday 17:00 America/New_York, and a trade date ``D``
covers ``D-1 17:00`` to ``D 17:00`` New York time, so a Sunday 17:00 bar belongs to Monday.
The rule is ``(New York wall clock + 7 hours).date()``, the same rule the FirstRate daily bars
follow. Pure pandas: reporting, mark-to-market (M3) and strategies (M6) share it.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

NEW_YORK = "America/New_York"
_ROLL = pd.Timedelta(hours=7)  # 17:00 New York + 7h = the next calendar day's 00:00


def fx_trade_date(ts_ns: int) -> date:
    """The FX trade date of the UTC nanosecond timestamp ``ts_ns``."""
    wall = pd.Timestamp(ts_ns, unit="ns", tz="UTC").tz_convert(NEW_YORK).tz_localize(None)
    return (wall + _ROLL).date()


def fx_trade_days(ts_ns: np.ndarray) -> pd.DatetimeIndex:
    """:func:`fx_trade_date` of each timestamp, as UTC midnight of that date (vectorised)."""
    wall = pd.to_datetime(ts_ns, unit="ns", utc=True).tz_convert(NEW_YORK).tz_localize(None)
    return (wall + _ROLL).normalize().tz_localize("UTC")
