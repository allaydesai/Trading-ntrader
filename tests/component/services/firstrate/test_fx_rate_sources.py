"""FX M3 (task 3.4): the loader brings a cross's conversion pair from the same catalog.

An EURGBP run settles in GBP, so its USD account needs GBPUSD bars from the same catalog and
timeframe as conversion-only data (``DataLoadResult.rate_sources``). Without them the run
fails before the engine starts, naming GBPUSD. USD-quote and USD-base pairs, and every
non-FX row, load exactly as before (no extra catalog read).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity

from src.services.exceptions import DataNotFoundError
from src.services.firstrate.backtest_loader import load_from_catalog

pytestmark = pytest.mark.component

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 3, tzinfo=timezone.utc)
IDS = {
    "EURGBP": "EUR/GBP.IDEALPRO",
    "GBPUSD": "GBP/USD.IDEALPRO",
    "USDJPY": "USD/JPY.IDEALPRO",
    "EURUSD": "EUR/USD.IDEALPRO",
    "AAPL": "AAPL.NASDAQ",
}
PRICES = {"EURGBP": "0.85710", "GBPUSD": "1.26512", "USDJPY": "149.871", "EURUSD": "1.08431"}


def _bars(ticker: str) -> list[Bar]:
    price_type = "LAST" if ticker == "AAPL" else "MID"
    bar_type = BarType.from_str(f"{IDS[ticker]}-1-HOUR-{price_type}-EXTERNAL")
    p = Price.from_str(PRICES.get(ticker, "190.25"))
    ts = int(START.timestamp() * 1_000_000_000)
    return [Bar(bar_type, p, p, p, p, Quantity.from_int(10), ts + i, ts + i) for i in range(2)]


def _row(ticker: str):
    row = MagicMock()
    row.ticker = ticker
    row.nautilus_id = IDS[ticker]
    row.asset_class = "STOCK" if ticker == "AAPL" else "FX"
    return row


def _sources(tickers: set[str]):
    """A catalog and metadata service holding ``tickers``; bars are looked up by bar type."""
    by_bar_type = {}
    for ticker in tickers:
        spec = "1-HOUR-LAST" if ticker == "AAPL" else "1-HOUR-MID"
        by_bar_type[f"{IDS[ticker]}-{spec}-EXTERNAL"] = _bars(ticker)
    catalog = MagicMock()
    catalog.bars.side_effect = lambda bar_types, start, end: by_bar_type.get(bar_types[0], [])
    catalog_manager = MagicMock()
    catalog_manager.resolve_catalog.return_value = catalog
    metadata = MagicMock()
    metadata.get_instrument_sync.side_effect = lambda _c, t: _row(t) if t in tickers else None
    return catalog, catalog_manager, metadata


async def _load(ticker: str, held: set[str], spec: str = "1-HOUR-MID"):
    catalog, catalog_manager, metadata = _sources(held)
    result = await load_from_catalog(
        catalog_name="firstrate-fx",
        ticker=ticker,
        bar_type_spec=spec,
        start=START,
        end=END,
        catalog_manager=catalog_manager,
        metadata_service=metadata,
    )
    return result, catalog


async def test_a_cross_loads_its_conversion_pair_from_the_same_catalog_and_timeframe():
    result, catalog = await _load("EURGBP", {"EURGBP", "GBPUSD"})
    assert str(result.instrument.id) == "EUR/GBP.IDEALPRO"
    assert len(result.rate_sources) == 1
    bars, instrument = result.rate_sources[0]
    assert str(instrument.id) == "GBP/USD.IDEALPRO"
    assert str(bars[0].bar_type) == "GBP/USD.IDEALPRO-1-HOUR-MID-EXTERNAL"
    assert [c.kwargs["bar_types"] for c in catalog.bars.call_args_list] == [
        ["EUR/GBP.IDEALPRO-1-HOUR-MID-EXTERNAL"],
        ["GBP/USD.IDEALPRO-1-HOUR-MID-EXTERNAL"],
    ]


async def test_a_cross_without_its_conversion_pair_fails_naming_it():
    with pytest.raises(DataNotFoundError, match="GBPUSD") as err:
        await _load("EURGBP", {"EURGBP"})
    assert "firstrate-fx" in str(err.value)
    assert "EURGBP" in str(err.value)


@pytest.mark.parametrize("ticker", ["USDJPY", "EURUSD"])
async def test_direct_pairs_need_no_rate_source(ticker):
    result, catalog = await _load(ticker, {ticker, "GBPUSD"})
    assert result.rate_sources == []
    assert catalog.bars.call_count == 1


async def test_an_equity_row_is_loaded_as_before():
    result, catalog = await _load("AAPL", {"AAPL"}, spec="1-HOUR-LAST")
    assert result.rate_sources == []
    assert catalog.bars.call_count == 1
