"""Crypto venues are refused until M3 wires the spot gate and model A (crypto M1).

The M1 spike (``tests/integration/core/test_crypto_engine_acceptance.py``) showed that a
``MARGIN`` venue enforces nothing for spot crypto without the gate: a run would overspend
and short at Nautilus' default leverage. M1 builds crypto instruments from catalog rows, so
until M3 lands every venue listing one is refused before ``add_venue`` is called.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.backtest_venue import add_backtest_venue, setup_backtest_venues
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair

pytestmark = pytest.mark.component

KRAKEN = Venue("KRAKEN")


def _kwargs() -> dict:
    return dict(
        starting_balance=Decimal("10000"), fill_model=FillModel(), equity_fee_model=MagicMock()
    )


def test_a_crypto_venue_is_refused_before_add_venue():
    engine = MagicMock()
    with pytest.raises(ValueError, match="Spot crypto backtests are not enabled before crypto M3"):
        add_backtest_venue(engine, KRAKEN, [build_crypto_pair(get_coin_spec("BTC"))], **_kwargs())
    engine.add_venue.assert_not_called()


def test_setup_refuses_a_crypto_venue():
    engine = MagicMock()
    venues = {KRAKEN: [build_crypto_pair(get_coin_spec("ETH"))]}
    with pytest.raises(ValueError, match="crypto M3"):
        setup_backtest_venues(engine, venues, **_kwargs())
    engine.add_venue.assert_not_called()


def test_a_crypto_pair_without_the_spec_info_is_not_refused():
    """The Kraken fetch path's fallback pair (no ``info.asset_class``) keeps today's path."""
    engine = MagicMock()
    engine.cache = MagicMock()
    pair = TestInstrumentProvider.btcusdt_binance()
    add_backtest_venue(engine, pair.id.venue, [pair], **_kwargs())
    engine.add_venue.assert_called_once()
