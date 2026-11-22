"""SMACrossover sizing inputs are Decimals however the config spelled them.

Config-file runs (``backtest run config.yaml``) hand YAML values to ``SMAConfig``
uncoerced, so ``position_size_pct: 50.0`` arrives as a float (or a str if quoted) and
``float / Decimal`` raised on the first signal. Found by the futures M3 smoke run.
"""

from decimal import Decimal

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.core.strategies.sma_crossover import SMAConfig, SMACrossover

pytestmark = pytest.mark.component


@pytest.mark.parametrize("raw", [50.0, "50.0", 50, Decimal("50.0")])
def test_sizing_inputs_are_decimals(raw):
    config = SMAConfig(
        instrument_id=InstrumentId.from_str("ES.CME"),
        bar_type=BarType.from_str("ES.CME-1-HOUR-LAST-EXTERNAL"),
        portfolio_value=raw,
        position_size_pct=raw,
    )
    strategy = SMACrossover(config)
    assert strategy.portfolio_value == Decimal("50")
    assert strategy.position_size_pct == Decimal("50")
    assert isinstance(strategy.portfolio_value, Decimal)
    assert isinstance(strategy.position_size_pct, Decimal)
