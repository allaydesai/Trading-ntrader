"""Verification tests for SMACrossover._calculate_position_size.

Pure verification — no production code change. Confirms the existing
size-precision branching at ``src/core/strategies/sma_crossover.py:140-161``
remains correct for both equity (whole-share) and crypto (fractional)
instruments. Backs Story 3.3 AC #8 / #9 / #10.

We invoke the unbound method against a duck-typed ``self`` because Nautilus
Strategy objects expose ``cache`` via a Cython slot that cannot be
overridden after construction; ``__new__``-then-setattr fails on those
attributes. The pure-Python sizing logic only reads attributes — so a
``SimpleNamespace`` stand-in is sufficient and isolates the test from the
C engine entirely.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.strategies.sma_crossover import SMACrossover


def _build_sizing_self(
    *,
    size_precision: int | None,
    close_price: float,
    portfolio_value: Decimal = Decimal("1000000"),
    position_size_pct: Decimal = Decimal("10"),
):
    """Build a duck-typed ``self`` for unbound _calculate_position_size."""
    instrument: object | None
    if size_precision is None:
        instrument = None
    else:
        instrument = SimpleNamespace(size_precision=size_precision)

    cache = MagicMock()
    cache.instrument = MagicMock(return_value=instrument)

    return SimpleNamespace(
        _current_bar=SimpleNamespace(close=close_price),
        portfolio_value=portfolio_value,
        position_size_pct=position_size_pct,
        instrument_id="AAPL.NASDAQ",
        cache=cache,
        log=MagicMock(),
    )


@pytest.mark.unit
class TestEquityWholeShares:
    """AC #8 — equities use whole-share quantities (size_precision=0)."""

    def test_equity_whole_shares(self):
        """At $165 close, $100K notional sizes to 606 whole shares."""
        sizing_self = _build_sizing_self(size_precision=0, close_price=165.0)

        qty = SMACrossover._calculate_position_size(sizing_self)

        # $1M * 10% / $165 = 606.06... → int(606) = 606
        assert int(str(qty)) == 606
        assert "." not in str(qty)

    def test_equity_default_precision_zero_when_instrument_missing(self):
        """An absent instrument (None) defaults to size_precision=0 path."""
        sizing_self = _build_sizing_self(size_precision=None, close_price=100.0)

        qty = SMACrossover._calculate_position_size(sizing_self)

        # $1M * 10% / $100 = 1000 whole shares
        assert int(str(qty)) == 1000
        assert "." not in str(qty)


@pytest.mark.unit
class TestCryptoFractionalShares:
    """AC #9 — crypto uses fractional quantities at instrument precision."""

    def test_crypto_fractional_shares_8_decimals(self):
        """At $60K close, size_precision=8 produces 8 fractional digits."""
        sizing_self = _build_sizing_self(size_precision=8, close_price=60_000.0)

        qty = SMACrossover._calculate_position_size(sizing_self)

        qty_str = str(qty)
        assert "." in qty_str
        decimal_part = qty_str.split(".")[1]
        assert len(decimal_part) == 8

    def test_crypto_fractional_shares_6_decimals(self):
        """size_precision=6 produces 6 fractional digits (Kraken altcoins)."""
        sizing_self = _build_sizing_self(size_precision=6, close_price=2_500.0)

        qty = SMACrossover._calculate_position_size(sizing_self)

        qty_str = str(qty)
        decimal_part = qty_str.split(".")[1]
        assert len(decimal_part) == 6


@pytest.mark.unit
class TestEquityMinimumShareFloor:
    """AC #8 corollary — the ``max(int(raw_qty), 1)`` floor at line 160."""

    def test_below_one_share_floors_to_one(self):
        """A tiny portfolio + expensive stock still yields 1 share, not 0."""
        sizing_self = _build_sizing_self(
            size_precision=0,
            close_price=200.0,
            portfolio_value=Decimal("0.01"),
            position_size_pct=Decimal("10"),
        )

        qty = SMACrossover._calculate_position_size(sizing_self)

        # $0.01 * 10% / $200 = 5e-6 → int(5e-6) = 0 → max(0, 1) = 1
        assert int(str(qty)) == 1

    def test_no_current_bar_raises(self):
        """Calling sizing before the first bar arrives is a programming bug."""
        sizing_self = SimpleNamespace(
            _current_bar=None,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10"),
            instrument_id="AAPL.NASDAQ",
            cache=MagicMock(),
            log=MagicMock(),
        )

        with pytest.raises(ValueError, match="without current bar"):
            SMACrossover._calculate_position_size(sizing_self)
