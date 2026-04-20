"""Integration tests for named-catalog BacktestEngine flow (Story 3.1, Task 8).

Runs the full flow against a tmp_path-scoped ParquetDataCatalog with synthetic
bars. Requires --forked (Nautilus C/Rust extension isolation).
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.db.models.catalog_instrument import CatalogInstrument
from src.services.exceptions import DataNotFoundError
from src.services.firstrate.backtest_loader import load_from_catalog
from src.services.firstrate.catalog_manager import CatalogManager


def _make_daily_bars(
    nautilus_id_str: str = "AAPL.NASDAQ",
    num_bars: int = 10,
    start_ns: int = 1_514_764_800_000_000_000,  # 2018-01-01 UTC
) -> list[Bar]:
    """Generate deterministic synthetic daily bars for integration tests."""
    bar_type = BarType.from_str(f"{nautilus_id_str}-1-DAY-LAST-EXTERNAL")
    one_day_ns = 86_400_000_000_000
    bars = []
    base_price = 100.0
    for i in range(num_bars):
        price = base_price + (i * 0.5)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(price, precision=2),
                high=Price(price + 1.0, precision=2),
                low=Price(price - 1.0, precision=2),
                close=Price(price + 0.25, precision=2),
                volume=Quantity(1_000_000, precision=0),
                ts_event=start_ns + (i * one_day_ns),
                ts_init=start_ns + (i * one_day_ns),
            )
        )
    return bars


def _make_instrument_row(
    ticker: str, nautilus_id: str, catalog_name: str = "e2e-test"
) -> CatalogInstrument:
    row = MagicMock(spec=CatalogInstrument)
    row.ticker = ticker
    row.nautilus_id = nautilus_id
    row.catalog_name = catalog_name
    row.date_range_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
    row.date_range_end = datetime(2018, 12, 31, tzinfo=timezone.utc)
    return row


@pytest.fixture
def synthetic_catalog(tmp_path: Path):
    """Build a tmp_path/catalogs/e2e-test ParquetDataCatalog with AAPL + MSFT daily bars."""
    base = tmp_path / "catalogs"
    base.mkdir()
    catalog_dir = base / "e2e-test"
    catalog_dir.mkdir()
    cat = ParquetDataCatalog(path=str(catalog_dir), fs_protocol="file")

    cat.write_data(_make_daily_bars("AAPL.NASDAQ"))
    cat.write_data(_make_daily_bars("MSFT.NASDAQ"))
    return base


def _build_sma_request(
    *,
    symbol: str = "AAPL",
    catalog_name: str = "e2e-test",
):
    from decimal import Decimal

    from src.models.backtest_request import BacktestRequest

    return BacktestRequest.from_cli_args(
        strategy="sma_crossover",
        symbol=symbol,
        start=datetime(2018, 1, 1, tzinfo=timezone.utc),
        end=datetime(2018, 1, 11, tzinfo=timezone.utc),
        bar_type_spec="1-DAY-LAST",
        persist=False,
        starting_balance=Decimal("100000"),
        data_source="catalog",
        catalog_name=catalog_name,
        portfolio_value=Decimal("100000"),
        position_size_pct=Decimal("10.0"),
        fast_period=2,
        slow_period=5,
    )


@pytest.mark.integration
class TestNamedCatalogBacktestIntegration:
    """End-to-end integration via CatalogManager → load_from_catalog → BacktestOrchestrator."""

    @pytest.mark.asyncio
    async def test_named_catalog_single_instrument_run(self, synthetic_catalog: Path):
        """Single-instrument AAPL daily backtest runs to completion."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "AAPL", "AAPL.NASDAQ"
        )

        load_result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="AAPL",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 1, 11, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        assert len(load_result.bars) == 10
        assert load_result.data_source_used == "Catalog: e2e-test"

        request = _build_sma_request()
        orchestrator = BacktestOrchestrator()
        try:
            result, run_id = await orchestrator.execute(
                request, load_result.bars, load_result.instrument
            )
            assert result is not None
            assert result.total_trades >= 0
            assert result.final_balance > 0
            assert run_id is None  # persist=False
        finally:
            orchestrator.dispose()

    @pytest.mark.asyncio
    async def test_named_catalog_multi_instrument_run(self, synthetic_catalog: Path):
        """Multi-instrument single-engine run with two tickers on NASDAQ."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()

        def _get_sync(catalog, ticker):
            return _make_instrument_row(ticker, f"{ticker}.NASDAQ")

        metadata_service.get_instrument_sync.side_effect = _get_sync

        aapl = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="AAPL",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 1, 11, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )
        msft = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="MSFT",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 1, 11, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        # Synthesise fresh equity instances for each symbol — the loader's default
        # uses TestInstrumentProvider.equity which may cache; this is safe for real bars.
        inst_aapl = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
        inst_msft = TestInstrumentProvider.equity(symbol="MSFT", venue="NASDAQ")

        request = _build_sma_request(symbol="AAPL")
        orchestrator = BacktestOrchestrator()
        try:
            result, _ = await orchestrator.execute_multi(
                request,
                instrument_bars=[(inst_aapl, aapl.bars), (inst_msft, msft.bars)],
            )
            assert result is not None
            assert result.total_trades >= 0
            assert result.final_balance > 0
        finally:
            orchestrator.dispose()

    @pytest.mark.asyncio
    async def test_named_catalog_missing_ticker_raises(self, synthetic_catalog: Path):
        """Ticker not in DB metadata → DataNotFoundError with catalog context."""
        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = None

        with pytest.raises(DataNotFoundError) as exc_info:
            await load_from_catalog(
                catalog_name="e2e-test",
                ticker="SPY",
                bar_type_spec="1-DAY-LAST",
                start=datetime(2018, 1, 1, tzinfo=timezone.utc),
                end=datetime(2018, 1, 11, tzinfo=timezone.utc),
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        assert exc_info.value.instrument_id == "SPY"
        assert "e2e-test" in str(exc_info.value)
        assert exc_info.value.context.get("missing_from_catalog") == "e2e-test"

    @pytest.mark.asyncio
    async def test_named_catalog_unknown_name_raises_user_error(self, synthetic_catalog: Path):
        """Unknown catalog → ValueError listing available catalogs."""
        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "AAPL", "AAPL.NASDAQ"
        )

        with pytest.raises(ValueError, match=r"Unknown catalog.*Available"):
            await load_from_catalog(
                catalog_name="does-not-exist",
                ticker="AAPL",
                bar_type_spec="1-DAY-LAST",
                start=datetime(2018, 1, 1, tzinfo=timezone.utc),
                end=datetime(2018, 1, 11, tzinfo=timezone.utc),
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

    @pytest.mark.asyncio
    async def test_named_catalog_empty_window_raises(self, synthetic_catalog: Path):
        """Valid ticker + catalog but requested window has no bars → DataNotFoundError."""
        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "AAPL", "AAPL.NASDAQ"
        )

        # Request a future window outside the synthetic 2018-01-01..2018-01-11 range
        with pytest.raises(DataNotFoundError) as exc_info:
            await load_from_catalog(
                catalog_name="e2e-test",
                ticker="AAPL",
                bar_type_spec="1-DAY-LAST",
                start=datetime(2027, 1, 1, tzinfo=timezone.utc),
                end=datetime(2027, 6, 30, tzinfo=timezone.utc),
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        assert exc_info.value.context.get("catalog") == "e2e-test"
        assert exc_info.value.context.get("metadata_range") is not None
