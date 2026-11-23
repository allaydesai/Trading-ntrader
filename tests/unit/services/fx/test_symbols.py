"""``conversion_ticker``: a cross's USD conversion pair, by ticker (QA fix d)."""

import pytest

from src.services.fx.symbols import conversion_ticker

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [("EURGBP", "GBPUSD"), ("EURUSD", None), ("USDJPY", None), ("GBPUSD", None), ("AAPL", None)],
)
def test_only_a_cross_needs_a_conversion_pair(ticker, expected):
    assert conversion_ticker(ticker) == expected
