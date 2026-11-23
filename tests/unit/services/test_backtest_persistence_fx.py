"""FX M3 (task 3.5): trade rows store ``profit_loss`` in USD (decision D11).

Nautilus reports a USDJPY position's realised P&L in JPY (``"100000.00 JPY"``). The writer
parses the currency, converts non-USD P&L at the position's exit rate (``usd_rates``, from
``src/core/fx_results.exit_rates``) and refuses a non-USD P&L it has no rate for, instead of
storing 100,000 "dollars". USD rows are parsed exactly as before. The service-level writes
are in ``tests/component/services/test_backtest_persistence_fx.py``.
"""

from decimal import Decimal

import pytest

from src.services.backtest_persistence import usd_profit_loss

pytestmark = pytest.mark.unit

RATE_151 = Decimal(str(1 / 151))  # what get_xrate returned at the USDJPY 151.000 exit


def test_usd_pnl_is_parsed_as_before():
    assert usd_profit_loss("-134.66 USD", None) == Decimal("-134.66")
    assert usd_profit_loss("100.00 USD", Decimal("2")) == Decimal("100.00")  # rate unused


def test_no_realized_pnl_is_zero():
    assert usd_profit_loss(None, None) == Decimal("0.00")
    assert usd_profit_loss(float("nan"), None) == Decimal("0.00")


def test_a_jpy_pnl_converts_at_the_exit_rate_to_cents():
    assert usd_profit_loss("100000.00 JPY", RATE_151) == Decimal("662.25")
    assert usd_profit_loss("-100000.00 JPY", RATE_151) == Decimal("-662.25")


def test_a_gbp_pnl_converts_at_the_gbpusd_rate():
    assert usd_profit_loss("1000.00 GBP", Decimal("1.25")) == Decimal("1250.00")


def test_a_non_usd_pnl_without_a_rate_raises_naming_the_currency():
    with pytest.raises(ValueError, match="JPY"):
        usd_profit_loss("100000.00 JPY", None)
