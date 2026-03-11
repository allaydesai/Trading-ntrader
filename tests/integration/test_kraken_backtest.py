"""
Integration tests for end-to-end Kraken crypto backtest execution.

These tests exercise the full backtest pipeline with Kraken data:
- BacktestRequest with data_source="kraken"
- DataCatalogService.fetch_or_load(data_source="kraken")
- BacktestOrchestrator.execute()
- Result extraction and metrics
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Price, Quantity

from src.core.backtest_orchestrator import BacktestOrchestrator
from src.models.backtest_request import BacktestRequest
from src.models.backtest_result import BacktestResult


def _make_kraken_bar(
    instrument_id_str: str = "BTC/USD.KRAKEN",
    bar_spec: str = "1-DAY-LAST",
    price: float = 50000.0,
    ts_ns: int = 1704067200000000000,
) -> Bar:
    """Create a Kraken bar for testing."""
    bar_type = BarType.from_str(f"{instrument_id_str}-{bar_spec}-EXTERNAL")
    return Bar(
        bar_type=bar_type,
        open=Price(price, precision=1),
        high=Price(price + 500, precision=1),
        low=Price(price - 500, precision=1),
        close=Price(price + 100, precision=1),
        volume=Quantity.from_str("10.00000000"),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _make_kraken_bars(count: int = 30) -> list[Bar]:
    """Generate a series of Kraken BTC/USD bars for backtesting."""
    bars = []
    base_price = 50000.0
    base_time = 1704067200000000000  # 2024-01-01 in nanoseconds
    day_ns = 86_400_000_000_000  # 1 day in nanoseconds

    for i in range(count):
        price_variation = (i % 7 - 3) * 200  # Oscillate around base
        price = base_price + price_variation
        bars.append(
            _make_kraken_bar(
                price=price,
                ts_ns=base_time + (i * day_ns),
            )
        )
    return bars


def _make_kraken_instrument() -> CurrencyPair:
    """Create a CurrencyPair instrument for BTC/USD.KRAKEN."""
    instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol("BTCUSD"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=Currency.from_str("USD"),
        price_precision=1,
        size_precision=8,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.00000001"),
        maker_fee=Decimal("0.0016"),
        taker_fee=Decimal("0.0026"),
        ts_event=0,
        ts_init=0,
    )


class TestKrakenBacktestIntegration:
    """Integration tests for end-to-end Kraken crypto backtest."""

    @pytest.fixture
    def kraken_bars(self):
        return _make_kraken_bars(count=30)

    @pytest.fixture
    def kraken_instrument(self):
        return _make_kraken_instrument()

    @pytest.fixture
    def kraken_request(self):
        """Create a BacktestRequest configured for Kraken."""
        return BacktestRequest(
            strategy_type="sma_crossover",
            strategy_path="src.core.strategies.sma_crossover:SMACrossover",
            config_path="src.core.strategies.sma_crossover:SMACrossoverConfig",
            strategy_config={
                "fast_period": 5,
                "slow_period": 10,
                "portfolio_value": Decimal("100000"),
                "position_size_pct": Decimal("10.0"),
            },
            symbol="BTC/USD",
            instrument_id="BTC/USD.KRAKEN",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            persist=True,
            data_source="kraken",
            starting_balance=Decimal("100000"),
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_kraken_backtest_end_to_end(self, kraken_request, kraken_bars, kraken_instrument):
        """Full Kraken backtest: request → orchestrator → result with metrics."""
        # Mock database session and persistence
        with patch("src.core.backtest_orchestrator.get_session") as mock_get_session:
            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None
            mock_get_session.return_value = mock_session

            with patch("src.core.backtest_orchestrator.BacktestRepository"):
                with patch(
                    "src.core.backtest_orchestrator.BacktestPersistenceService"
                ) as mock_svc_cls:
                    mock_svc = AsyncMock()
                    mock_svc.save_backtest_results = AsyncMock(return_value=Mock(id=uuid4()))
                    mock_svc.save_trades_from_positions = AsyncMock(return_value=5)
                    mock_svc_cls.return_value = mock_svc

                    orchestrator = BacktestOrchestrator()
                    try:
                        result, run_id = await orchestrator.execute(
                            kraken_request, kraken_bars, kraken_instrument
                        )

                        # Assert result structure
                        assert result is not None
                        assert isinstance(result, BacktestResult)
                        assert isinstance(result.total_return, (float, int))
                        assert isinstance(result.total_trades, int)
                        assert result.total_trades >= 0
                        assert isinstance(result.final_balance, (float, int))
                        assert result.final_balance > 0

                        # Metrics present
                        assert hasattr(result, "win_rate")
                        assert hasattr(result, "sharpe_ratio")
                        assert hasattr(result, "max_drawdown")
                        assert result.winning_trades + result.losing_trades == result.total_trades

                        # Persistence was called
                        assert mock_svc.save_backtest_results.called
                        assert run_id is not None
                    finally:
                        orchestrator.dispose()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_kraken_backtest_request_has_correct_data_source(self, kraken_request):
        """BacktestRequest with data_source='kraken' round-trips correctly."""
        assert kraken_request.data_source == "kraken"

        # Config snapshot includes data_source
        snapshot = kraken_request.to_config_snapshot()
        assert snapshot["data_source"] == "kraken"
        assert snapshot["instrument_id"] == "BTC/USD.KRAKEN"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_kraken_data_loads_via_catalog_service(self, kraken_bars, kraken_instrument):
        """DataCatalogService.fetch_or_load routes to Kraken client."""
        mock_kraken_client = AsyncMock()
        mock_kraken_client.is_connected = True
        mock_kraken_client.connect = AsyncMock(return_value={"connected": True})
        mock_kraken_client.fetch_bars = AsyncMock(return_value=(kraken_bars, kraken_instrument))

        mock_catalog = MagicMock()
        mock_catalog.write_data = MagicMock()

        with patch(
            "src.services.data_catalog.ParquetDataCatalog",
            return_value=mock_catalog,
        ):
            from src.services.data_catalog import DataCatalogService

            svc = DataCatalogService(
                catalog_path="/tmp/test_catalog",
                kraken_client=mock_kraken_client,
            )
            svc.catalog = mock_catalog

            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 31, tzinfo=timezone.utc)

            with patch.object(svc, "_rebuild_availability_cache"):
                bars = await svc.fetch_or_load(
                    instrument_id="BTC/USD.KRAKEN",
                    start=start,
                    end=end,
                    bar_type_spec="1-DAY-LAST",
                    data_source="kraken",
                )

            assert len(bars) == 30
            mock_kraken_client.fetch_bars.assert_called_once()
