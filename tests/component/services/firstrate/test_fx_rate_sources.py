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


HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS


def _bars(ticker: str, first_day: int = 0, days: int = 2) -> list[Bar]:
    """Two bars a day (00:00 and 01:00 UTC) from ``START + first_day`` for ``days`` days."""
    price_type = "LAST" if ticker == "AAPL" else "MID"
    bar_type = BarType.from_str(f"{IDS[ticker]}-1-HOUR-{price_type}-EXTERNAL")
    p = Price.from_str(PRICES.get(ticker, "190.25"))
    ts = int(START.timestamp() * 1_000_000_000) + first_day * DAY_NS
    stamps = [ts + d * DAY_NS + h * HOUR_NS for d in range(days) for h in range(2)]
    return [Bar(bar_type, p, p, p, p, Quantity.from_int(10), t, t) for t in stamps]


def _row(ticker: str):
    row = MagicMock()
    row.ticker = ticker
    row.nautilus_id = IDS[ticker]
    row.asset_class = "STOCK" if ticker == "AAPL" else "FX"
    return row


def _sources(tickers: set[str], spans: dict[str, tuple[int, int]] | None = None):
    """A catalog and metadata service holding ``tickers``; bars are looked up by bar type.

    ``spans`` gives a ticker's ``(first_day, days)``; the default is two days from START.
    """
    by_bar_type = {}
    for ticker in tickers:
        spec = "1-HOUR-LAST" if ticker == "AAPL" else "1-HOUR-MID"
        by_bar_type[f"{IDS[ticker]}-{spec}-EXTERNAL"] = _bars(
            ticker, *(spans or {}).get(ticker, (0, 2))
        )
    catalog = MagicMock()
    catalog.bars.side_effect = lambda bar_types, start, end: by_bar_type.get(bar_types[0], [])
    catalog_manager = MagicMock()
    catalog_manager.resolve_catalog.return_value = catalog
    metadata = MagicMock()
    metadata.get_instrument_sync.side_effect = lambda _c, t: _row(t) if t in tickers else None
    return catalog, catalog_manager, metadata


async def _load(ticker: str, held: set[str], spec: str = "1-HOUR-MID", spans=None):
    catalog, catalog_manager, metadata = _sources(held, spans)
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


# QA fix (d): a conversion series that covers only part of the traded window is refused at
# load time, naming the pair and the dates it covers, instead of failing at the first signal.


async def test_a_conversion_pair_that_starts_after_the_cross_is_refused_up_front():
    spans = {"EURGBP": (0, 3), "GBPUSD": (1, 2)}
    with pytest.raises(DataNotFoundError) as err:
        await _load("EURGBP", {"EURGBP", "GBPUSD"}, spans=spans)
    message = str(err.value)
    assert "GBPUSD" in message and "EURGBP" in message
    assert "covers 2024-01-02 to 2024-01-03" in message
    assert "2024-01-01 to 2024-01-03" in message
    assert err.value.context["conversion_coverage"] == ("2024-01-02", "2024-01-03")


async def test_a_conversion_pair_that_ends_before_the_cross_is_refused_up_front():
    spans = {"EURGBP": (0, 3), "GBPUSD": (0, 2)}
    with pytest.raises(DataNotFoundError, match="covers 2024-01-01 to 2024-01-02"):
        await _load("EURGBP", {"EURGBP", "GBPUSD"}, spans=spans)


async def test_a_conversion_pair_spanning_the_cross_s_dates_is_accepted():
    spans = {"EURGBP": (0, 3), "GBPUSD": (0, 3)}
    result, _ = await _load("EURGBP", {"EURGBP", "GBPUSD"}, spans=spans)
    assert len(result.rate_sources) == 1
