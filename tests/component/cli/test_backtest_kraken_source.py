"""``--data-source kraken`` builds the spec pair and charges the Kraken schedule (crypto M7, 7.4).

Until M7 a Kraken-fetched pair carried no ``info``, so ``is_crypto`` was false and the venue
charged the equity per-share model. Now the client returns the coin spec's ``BTC/USD.KRAKEN``
(or a fallback spec, for a USD coin without one), the venue gets ``CryptoCommissionModel`` at
the run's tier, and nothing is persisted to the default catalog (whose old Kraken series, if
any, may be perpetual prices).
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from src.cli.commands._backtest_helpers import load_backtest_data
from src.cli.commands._crypto_options import with_crypto_terms
from src.core.crypto_costs import CryptoCommissionModel
from src.core.futures_costs import venue_cost_models
from src.services.crypto.kraken_spot import spot_bars, spot_instrument

pytestmark = pytest.mark.component

START = datetime(2026, 11, 24, tzinfo=timezone.utc)
END = datetime(2026, 11, 24, 23, tzinfo=timezone.utc)
DOGE_INFO = {
    "altname": "XDGUSD",
    "pair_decimals": 7,
    "ordermin": "50",
    "costmin": "0.5",
    "tick_size": "0.0000001",
}


def _fetched(user_pair: str, pair_info=None):
    pair = spot_instrument(user_pair, pair_info)
    first = int(START.timestamp())
    rows = [[first + 3600 * i, "1.1", "1.2", "1.0", "1.1", "0", "5", 3] for i in range(4)]
    bars = spot_bars(
        rows,
        last=first + 3600 * 3,
        instrument_id=str(pair.id),
        bar_type_spec="1-HOUR-LAST",
        start=START,
        end=END,
        price_precision=pair.price_precision,
    )
    return bars, pair


async def _load(fetched):
    with (
        patch(
            "src.services.kraken_client.fetch_kraken_spot", AsyncMock(return_value=fetched)
        ) as fetch,
        patch("src.cli.commands._backtest_helpers.DataCatalogService") as catalog,
    ):
        result = await load_backtest_data(
            data_source="kraken",
            instrument_id=str(fetched[1].id),
            bar_type_spec="1-HOUR-LAST",
            start=START,
            end=END,
            console=Console(quiet=True),
        )
    fetch.assert_awaited_once_with(str(fetched[1].id), START, END, "1-HOUR-LAST")
    catalog.assert_not_called()  # no read from, or write to, the default catalog
    return result


async def test_the_fetched_btc_pair_is_spec_built_and_charged_the_kraken_schedule():
    result = await _load(_fetched("BTC/USD"))
    assert str(result.instrument.id) == "BTC/USD.KRAKEN" and result.data_source_used == "Kraken"
    assert len(result.bars) == 3
    fee_model, margin = venue_cost_models([result.instrument], equity_fee_model=MagicMock())
    assert isinstance(fee_model, CryptoCommissionModel) and margin is None
    (terms,) = fee_model.terms.values()
    assert (terms.fee_tier, terms.maker_pct, terms.taker_pct) == (
        2,
        Decimal("0.30"),
        Decimal("0.60"),
    )


async def test_the_runs_fee_tier_reaches_the_fetched_pair():
    result = await _load(_fetched("BTC/USD"))
    tiered = with_crypto_terms(result.instrument, fee_tier=4)
    fee_model, _ = venue_cost_models([tiered], equity_fee_model=MagicMock())
    (terms,) = fee_model.terms.values()
    assert (terms.fee_tier, terms.taker_pct) == (4, Decimal("0.35"))


async def test_a_coin_without_a_spec_gets_the_fallback_on_the_same_schedule():
    result = await _load(_fetched("DOGE/USD", DOGE_INFO))
    assert str(result.instrument.id) == "DOGE/USD.KRAKEN"
    fee_model, _ = venue_cost_models([result.instrument], equity_fee_model=MagicMock())
    assert isinstance(fee_model, CryptoCommissionModel)
    (terms,) = fee_model.terms.values()
    assert (terms.fee_tier, terms.spread_bps) == (2, Decimal("5"))


def test_a_range_older_than_the_spot_window_is_a_usage_error_naming_the_history_path():
    """Exit 2 with the refusal, not the IBKR "market data not available" panel (exit 1)."""
    from click.testing import CliRunner

    from src.cli.commands.backtest import run_backtest
    from src.services.exceptions import KrakenSpotRangeError

    refusal = KrakenSpotRangeError(
        "BTC/USD.KRAKEN", START, END, message="only the latest 720 candles; use kraken-crypto"
    )
    with patch("src.services.kraken_client.fetch_kraken_spot", AsyncMock(side_effect=refusal)):
        result = CliRunner().invoke(
            run_backtest,
            [
                "--symbol",
                "BTC/USD",
                "--data-source",
                "kraken",
                "--timeframe",
                "1-hour",
                "--start",
                "2024-01-01",
                "--end",
                "2024-03-01",
                "--no-persist",
            ],
        )
    assert result.exit_code == 2, result.output
    assert "use kraken-crypto" in result.output
    assert "IBKR" not in result.output
