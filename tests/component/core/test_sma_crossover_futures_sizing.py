"""SMACrossover sizes futures by notional in whole contracts (futures M3).

Component tier: importing ``SMACrossover`` loads Nautilus. The sizing method is called
unbound on a duck-typed ``self`` (same technique as
``tests/unit/strategies/test_sma_crossover_position_sizing.py``), so no engine runs.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.core.strategies.sma_crossover import SMACrossover
from tests.unit.strategies.test_sma_crossover_position_sizing import _build_sizing_self


def _futures_sizing_self(
    *, multiplier: str, close_price: float, portfolio_value: str, pct: str = "10"
):
    """A sizing ``self`` whose instrument is a multiplied contract (futures M3)."""
    sizing_self = _build_sizing_self(
        size_precision=0,
        close_price=close_price,
        portfolio_value=Decimal(portfolio_value),
        position_size_pct=Decimal(pct),
    )
    sizing_self.cache.instrument.return_value = SimpleNamespace(
        size_precision=0, multiplier=Decimal(multiplier)
    )
    return sizing_self


@pytest.mark.component
class TestFuturesContractSizing:
    """Futures M3 — notional is qty x price x multiplier, in whole contracts."""

    def test_100k_on_es_is_below_one_contract_so_no_order(self):
        """$100K at ES 7000 is 0.29 contracts ($350K each), not ~14 'shares' or 1 forced."""
        sizing_self = _futures_sizing_self(
            multiplier="50", close_price=7000.0, portfolio_value="1000000"
        )

        assert SMACrossover._calculate_position_size(sizing_self) is None
        sizing_self.log.warning.assert_called_once()

    def test_es_floors_to_whole_contracts(self):
        """$1M x 100% at ES 7000 is 2.86 contracts: floor to 2, never round up."""
        sizing_self = _futures_sizing_self(
            multiplier="50", close_price=7000.0, portfolio_value="1000000", pct="100"
        )

        qty = SMACrossover._calculate_position_size(sizing_self)

        assert str(qty) == "2"

    def test_cl_uses_its_1000_multiplier(self):
        """$500K at CL 70 = $70K per contract -> 7 contracts (7.14 floored)."""
        sizing_self = _futures_sizing_self(
            multiplier="1000", close_price=70.0, portfolio_value="500000", pct="100"
        )

        assert str(SMACrossover._calculate_position_size(sizing_self)) == "7"

    def test_equity_with_multiplier_one_is_unchanged(self):
        """A multiplier of 1 (every Equity) keeps the whole-share, minimum-1 rule."""
        sizing_self = _futures_sizing_self(
            multiplier="1", close_price=165.0, portfolio_value="1000000"
        )

        assert int(str(SMACrossover._calculate_position_size(sizing_self))) == 606


@pytest.mark.component
def test_the_logged_notional_is_the_order_not_the_budget():
    """2 ES contracts at 7000 are $700,000 of exposure, not the $1,000,000 budget."""
    sizing_self = _futures_sizing_self(
        multiplier="50", close_price=7000.0, portfolio_value="1000000", pct="100"
    )

    SMACrossover._calculate_position_size(sizing_self)

    message = sizing_self.log.info.call_args[0][0]
    assert "~$700,000 notional" in message
