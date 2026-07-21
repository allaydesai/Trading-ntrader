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


def _daily_bars(nautilus_id: str, close: float = 150.0):
    """Build one deterministic daily Bar for the given nautilus_id (precision=2)."""
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity

    bar_type = BarType.from_str(f"{nautilus_id}-1-DAY-LAST-EXTERNAL")
    return [
        Bar(
            bar_type=bar_type,
            open=Price(close, precision=2),
            high=Price(close + 1.0, precision=2),
            low=Price(close - 1.0, precision=2),
            close=Price(close, precision=2),
            volume=Quantity(1_000_000, precision=0),
            ts_event=0,
            ts_init=0,
        )
    ]


@pytest.mark.unit
class TestEtfWholeShareSizingMatchesStocks:
    """Story 5.2 AC #2/#3 — ETFs size in whole shares, identically to Stocks.

    The sizing branch reads only ``instrument.size_precision``; a Nautilus
    ``Equity`` (Stock OR ETF, incl. leveraged/inverse) is always
    ``size_precision == 0`` → the whole-share branch. There is no ``asset_class``
    branch and no crypto/FX fractional path — that asset-class blindness IS the
    "consistent with Stocks / settles as ordinary shares" guarantee.
    """

    def test_etf_and_stock_size_identically(self):
        """A REAL ETF Equity and a REAL Stock Equity size to the SAME whole share count.

        Not a tautology: each side derives its ``size_precision`` from an actual
        ``build_equity`` instrument (SPY.ARCA ETF vs AAPL.NASDAQ stock) — proving the
        real ETF instrument drives the identical whole-share branch as a real stock,
        and that the count matches the exact hand-computed expectation (magnitude, not
        just integrality).
        """
        from src.services.firstrate.backtest_loader import build_equity

        etf = build_equity(nautilus_id="SPY.ARCA", ticker="SPY", bars=_daily_bars("SPY.ARCA"))
        stock = build_equity(
            nautilus_id="AAPL.NASDAQ", ticker="AAPL", bars=_daily_bars("AAPL.NASDAQ")
        )

        etf_self = _build_sizing_self(size_precision=etf.size_precision, close_price=140.0)
        stock_self = _build_sizing_self(size_precision=stock.size_precision, close_price=140.0)

        etf_qty = SMACrossover._calculate_position_size(etf_self)
        stock_qty = SMACrossover._calculate_position_size(stock_self)

        assert str(etf_qty) == str(stock_qty)  # identical share count across asset classes
        assert "." not in str(etf_qty)  # whole shares, no fractional part
        # $1M * 10% / $140 = 714.28… → int() = 714 whole shares (exact magnitude).
        assert int(str(etf_qty)) == 714

    @pytest.mark.parametrize(
        "nautilus_id",
        [
            "SPY.ARCA",  # plain ETF
            "TQQQ.NASDAQ",  # leveraged (3x) ETF
            "SQQQ.NASDAQ",  # inverse (-3x) ETF
        ],
    )
    def test_real_etf_equity_binds_to_whole_share_branch(self, nautilus_id: str):
        """A real Equity from build_equity (incl. leveraged/inverse) has size_precision=0.

        Reads size_precision off the actual synthesised instrument and feeds it into
        the sizing path — proving the loader's ETF Equity settles as ordinary shares
        (whole-share branch), not crypto/FX fractional rules. No leverage special-casing.
        """
        from src.services.firstrate.backtest_loader import build_equity

        ticker = nautilus_id.split(".")[0]
        instrument = build_equity(
            nautilus_id=nautilus_id, ticker=ticker, bars=_daily_bars(nautilus_id)
        )

        # Ordinary-shares settlement — no fractional/crypto precision, no leverage branch.
        assert instrument.size_precision == 0
        assert int(instrument.size_increment) == 1

        sizing_self = _build_sizing_self(
            size_precision=instrument.size_precision, close_price=140.0
        )
        qty = SMACrossover._calculate_position_size(sizing_self)

        assert "." not in str(qty)  # whole shares
        # Exact magnitude ($1M * 10% / $140 = 714.28… → 714), not just a >=1 floor.
        assert int(str(qty)) == 714
