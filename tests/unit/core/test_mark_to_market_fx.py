"""FX M3 (task 3.5): USD mark-to-market equity for spot FX, cut at the 17:00 ET trade date.

A USD single-currency account books each fill's quote-currency P&L in USD at the rate the
account converted that fill at (``Fill.quote_rate``), and an open position is worth its
unrealised quote P&L at the latest rate. ``fx_daily_equity`` rebuilds exactly that, so a
flat run ends on the engine's USD balance (``_check_against_accounts``). Pure pandas.
"""

from datetime import date

import pandas as pd
import pytest

from src.core.mark_to_market import Fill, daily_equity, fx_daily_equity
from src.services.fx.sessions import fx_trade_date

pytestmark = pytest.mark.unit

HOUR = 3_600_000_000_000
T0 = pd.Timestamp("2025-03-06 15:00", tz="UTC").value  # 10:00 ET, trade date 2025-03-06
USDJPY = "USD/JPY.IDEALPRO"
EURGBP = "EUR/GBP.IDEALPRO"
EURUSD = "EUR/USD.IDEALPRO"
START = 100_000.0


def _closes(values: list[float], start: int = T0) -> list[tuple[int, float]]:
    return [(start + i * HOUR, v) for i, v in enumerate(values)]


def _fill(i: int, instrument: str, qty: float, px: float, rate: float, pos: str = "P-1") -> Fill:
    return Fill(T0 + i * HOUR, instrument, qty, px, 0.0, quote_rate=rate, position_id=pos)


USDJPY_CLOSES = [150.0, 150.0, 151.0, 151.0]
USDJPY_RATES = {USDJPY: [(ts, 1 / px) for ts, px in _closes(USDJPY_CLOSES)]}


def test_a_usdjpy_round_trip_ends_on_the_converted_balance():
    fills = [_fill(0, USDJPY, 100_000, 150.0, 1 / 150), _fill(2, USDJPY, -100_000, 151.0, 1 / 151)]
    equity = fx_daily_equity(START, fills, {USDJPY: _closes(USDJPY_CLOSES)}, USDJPY_RATES)
    assert float(equity.iloc[-1]) == pytest.approx(START + 100_000 / 151)  # USD 662.25, not 100k


def test_an_open_usdjpy_position_is_marked_in_usd_at_the_latest_rate():
    fills = [_fill(0, USDJPY, 100_000, 150.0, 1 / 150)]
    equity = fx_daily_equity(START, fills, {USDJPY: _closes(USDJPY_CLOSES)}, USDJPY_RATES)
    assert float(equity.iloc[-1]) == pytest.approx(START + 100_000 * (151 - 150) / 151)


def test_realised_pnl_keeps_its_fill_rate_after_the_rate_moves():
    closes = [150.0, 151.0, 160.0]
    rates = {USDJPY: [(ts, 1 / px) for ts, px in _closes(closes)]}
    fills = [_fill(0, USDJPY, 100_000, 150.0, 1 / 150), _fill(1, USDJPY, -100_000, 151.0, 1 / 151)]
    equity = fx_daily_equity(START, fills, {USDJPY: _closes(closes)}, rates)
    assert float(equity.iloc[-1]) == pytest.approx(START + 100_000 / 151)  # not / 160


def test_a_cross_converts_through_its_rate_series():
    closes = [0.85, 0.85, 0.86, 0.86]
    gbpusd = [1.20, 1.22, 1.25, 1.30]
    rates = {EURGBP: list(zip([ts for ts, _ in _closes(closes)], gbpusd, strict=True))}
    fills = [_fill(0, EURGBP, 100_000, 0.85, 1.20), _fill(2, EURGBP, -100_000, 0.86, 1.25)]
    equity = fx_daily_equity(START, fills, {EURGBP: _closes(closes)}, rates)
    assert float(equity.iloc[-1]) == pytest.approx(START + 1_250.0)


def test_partial_closes_realise_each_part_at_its_own_rate():
    closes = [150.0, 151.0, 152.0]
    rates = {USDJPY: [(ts, 1 / px) for ts, px in _closes(closes)]}
    fills = [
        _fill(0, USDJPY, 100_000, 150.0, 1 / 150),
        _fill(1, USDJPY, -50_000, 151.0, 1 / 151),
        _fill(2, USDJPY, -50_000, 152.0, 1 / 152),
    ]
    equity = fx_daily_equity(START, fills, {USDJPY: _closes(closes)}, rates)
    assert float(equity.iloc[-1]) == pytest.approx(START + 50_000 / 151 + 100_000 / 152)


def test_a_short_round_trip_and_commission_in_usd():
    fills = [
        Fill(T0, USDJPY, -100_000, 151.0, 2.0, quote_rate=1 / 151, position_id="P-1"),
        Fill(T0 + 2 * HOUR, USDJPY, 100_000, 150.0, 2.0, quote_rate=1 / 150, position_id="P-1"),
    ]
    equity = fx_daily_equity(START, fills, {USDJPY: _closes(USDJPY_CLOSES)}, USDJPY_RATES)
    assert float(equity.iloc[-1]) == pytest.approx(START + 100_000 / 150 - 4.0)


def test_hedged_positions_on_one_pair_keep_their_own_average_cost():
    closes = [150.0, 151.0, 152.0]
    rates = {USDJPY: [(ts, 1 / px) for ts, px in _closes(closes)]}
    fills = [
        _fill(0, USDJPY, 100_000, 150.0, 1 / 150, pos="LONG"),
        _fill(1, USDJPY, -100_000, 151.0, 1 / 151, pos="SHORT"),  # a new position, not a close
        _fill(2, USDJPY, -100_000, 152.0, 1 / 152, pos="LONG"),
    ]
    equity = fx_daily_equity(START, fills, {USDJPY: _closes(closes)}, rates)
    long_pnl = 200_000 / 152  # (152 - 150) x 100k JPY at the exit rate
    short_open = -100_000 * (152 - 151) / 152  # still open, marked at the last rate
    assert float(equity.iloc[-1]) == pytest.approx(START + long_pnl + short_open)


def test_a_usd_quote_pair_matches_the_equity_rebuild():
    closes = [1.08, 1.09, 1.10, 1.07]
    fills = [_fill(0, EURUSD, 100_000, 1.08, 1.0), _fill(2, EURUSD, -50_000, 1.10, 1.0)]
    fx = fx_daily_equity(START, fills, {EURUSD: _closes(closes)}, {})
    plain = daily_equity(START, fills, {EURUSD: _closes(closes)})  # one UTC and one FX day
    assert list(fx) == pytest.approx(list(plain))


def test_days_are_cut_at_17_00_new_york_not_utc_midnight():
    # 16:00 and 18:00 ET on 2025-03-06 are the same UTC day but two FX trade dates.
    first = pd.Timestamp("2025-03-06 21:00", tz="UTC").value
    closes = [(first, 1.08), (first + 2 * HOUR, 1.09)]
    equity = fx_daily_equity(START, [], {EURUSD: closes}, {})
    assert [d.date() for d in equity.index] == [date(2025, 3, 6), date(2025, 3, 7)]
    assert len(daily_equity(START, [], {EURUSD: closes})) == 1


def test_fills_without_bars_raise():
    with pytest.raises(ValueError, match="no bars"):
        fx_daily_equity(START, [_fill(0, USDJPY, 1, 150.0, 1 / 150)], {USDJPY: []}, {})


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        ("2025-03-09 21:00", date(2025, 3, 10)),  # Sunday 17:00 EDT opens Monday
        ("2025-03-07 21:59", date(2025, 3, 7)),  # Friday 16:59 EST closes Friday
        ("2025-03-06 22:00", date(2025, 3, 7)),  # Thursday 17:00 EST starts Friday
        ("2025-07-10 20:59", date(2025, 7, 10)),  # 16:59 EDT
        ("2025-07-10 21:00", date(2025, 7, 11)),  # 17:00 EDT
    ],
)
def test_fx_trade_date_rolls_at_17_00_new_york(utc, expected):
    assert fx_trade_date(pd.Timestamp(utc, tz="UTC").value) == expected
