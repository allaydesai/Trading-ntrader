"""FX M3 (task 3.5): trade rows store ``profit_loss`` in USD (decision D11).

Nautilus reports a USDJPY position's realised P&L in JPY (``"100000.00 JPY"``). The writer
parses the currency, stores non-USD P&L as the USD amount the account booked (``usd_pnls``,
from ``src/core/fx_results.usd_realised_pnls``) and refuses a non-USD P&L without one, instead of
storing 100,000 "dollars". USD rows are parsed exactly as before. The service-level writes
are in ``tests/component/services/test_backtest_persistence_fx.py``.
"""

from decimal import Decimal

import pytest

from src.services.backtest_persistence import usd_profit_loss

pytestmark = pytest.mark.unit

USD_662 = Decimal(str(100_000 / 151))  # 100,000 JPY at the USDJPY 151.000 exit


def test_usd_pnl_is_parsed_as_before():
    assert usd_profit_loss("-134.66 USD", None) == Decimal("-134.66")
    assert usd_profit_loss("100.00 USD", Decimal("2")) == Decimal("100.00")  # rate unused


def test_no_realized_pnl_is_zero():
    assert usd_profit_loss(None, None) == Decimal("0.00")
    assert usd_profit_loss(float("nan"), None) == Decimal("0.00")


def test_a_jpy_pnl_is_stored_as_its_usd_amount_to_cents():
    assert usd_profit_loss("100000.00 JPY", USD_662) == Decimal("662.25")
    assert usd_profit_loss("-100000.00 JPY", -USD_662) == Decimal("-662.25")


def test_a_zero_quote_pnl_keeps_its_nonzero_usd_amount():
    # closed half at 151 and half at 149: 0 JPY, but the account booked about -4.44 USD
    assert usd_profit_loss("0.00 JPY", Decimal("-4.4150")) == Decimal("-4.42")


def test_a_non_usd_pnl_without_a_rate_raises_naming_the_currency():
    with pytest.raises(ValueError, match="JPY"):
        usd_profit_loss("100000.00 JPY", None)
