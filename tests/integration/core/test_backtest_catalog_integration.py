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


def _make_zigzag_bars(
    nautilus_id_str: str = "SPY.ARCA",
    num_bars: int = 40,
    start_ns: int = 1_514_764_800_000_000_000,  # 2018-01-01 UTC
) -> list[Bar]:
    """Generate zigzag daily bars so the SMA crossover actually fires (real fills).

    Monotonic bars produce at most one crossover; a whole-share fills assertion
    needs several orders. The saw-tooth reverses every few bars to force repeated
    fast/slow crossovers within the run window.
    """
    bar_type = BarType.from_str(f"{nautilus_id_str}-1-DAY-LAST-EXTERNAL")
    one_day_ns = 86_400_000_000_000
    bars = []
    for i in range(num_bars):
        # +5 for the first half of each 6-bar cycle, -5 for the second → repeated crossovers.
        price = 100.0 + (5.0 if i % 6 < 3 else -5.0) + (i * 0.1)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(price, precision=2),
                high=Price(price + 1.0, precision=2),
                low=Price(price - 1.0, precision=2),
                close=Price(price, precision=2),
                volume=Quantity(1_000_000, precision=0),
                ts_event=start_ns + (i * one_day_ns),
                ts_init=start_ns + (i * one_day_ns),
            )
        )
    return bars


def _make_instrument_row(
    ticker: str,
    nautilus_id: str | None,
    catalog_name: str = "e2e-test",
    asset_class: str = "STOCK",
) -> CatalogInstrument:
    row = MagicMock(spec=CatalogInstrument)
    row.ticker = ticker
    row.nautilus_id = nautilus_id
    row.catalog_name = catalog_name
    row.asset_class = asset_class
    row.date_range_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
    row.date_range_end = datetime(2018, 12, 31, tzinfo=timezone.utc)
    return row


@pytest.fixture
def synthetic_catalog(tmp_path: Path):
    """Build a tmp_path/catalogs/e2e-test ParquetDataCatalog with AAPL + MSFT + SPY daily bars.

    SPY.ARCA stands in for an ETF (Story 5.1) — served through the identical Equity
    path as the stock instruments.
    """
    base = tmp_path / "catalogs"
    base.mkdir()
    catalog_dir = base / "e2e-test"
    catalog_dir.mkdir()
    cat = ParquetDataCatalog(path=str(catalog_dir), fs_protocol="file")

    cat.write_data(_make_daily_bars("AAPL.NASDAQ"))
    cat.write_data(_make_daily_bars("MSFT.NASDAQ"))
    # SPY.ARCA = plain ETF (Story 5.1, 10-bar monotonic window). IVV.ARCA = plain ETF and
    # TQQQ.NASDAQ = leveraged (3x) ETF (Story 5.2) — both plain whole-share Equities served
    # through the identical Stocks path; each gets zigzag bars so the crossover fires
    # repeatedly and whole-share fills can be asserted across many orders.
    cat.write_data(_make_daily_bars("SPY.ARCA"))
    cat.write_data(_make_zigzag_bars("IVV.ARCA"))
    cat.write_data(_make_zigzag_bars("TQQQ.NASDAQ"))
    return base


def _build_sma_request(
    *,
    symbol: str = "AAPL",
    catalog_name: str = "e2e-test",
    start: datetime = datetime(2018, 1, 1, tzinfo=timezone.utc),
    end: datetime = datetime(2018, 1, 11, tzinfo=timezone.utc),
):
    from decimal import Decimal

    from src.models.backtest_request import BacktestRequest

    return BacktestRequest.from_cli_args(
        strategy="sma_crossover",
        symbol=symbol,
        start=start,
        end=end,
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
    async def test_named_catalog_etf_single_instrument_run(self, synthetic_catalog: Path):
        """Story 5.1 AC1/AC4: an ETF (SPY.ARCA) runs to completion via the SAME path.

        No ETF-specific adapter — the loader synthesises a whole-share Equity and the
        existing BacktestOrchestrator consumes the bars exactly as for a stock.
        """
        from nautilus_trader.model.instruments import Equity

        from src.core.backtest_orchestrator import BacktestOrchestrator

        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "SPY", "SPY.ARCA", asset_class="ETF"
        )

        load_result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="SPY",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 1, 11, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        assert len(load_result.bars) == 10
        # ETF served by the identical Equity synthesis (whole-share precondition).
        assert isinstance(load_result.instrument, Equity)
        assert str(load_result.instrument.id.venue) == "ARCA"
        assert int(load_result.instrument.lot_size) == 1

        request = _build_sma_request(symbol="SPY")
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
    async def test_named_catalog_unresolved_venue_etf_fails_fast(self, synthetic_catalog: Path):
        """Story 5.1 AC2: an unresolved-venue ETF (nautilus_id None) is excluded fast.

        The catalog is never read — an unresolved instrument can never silently enter a run.
        """
        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "SPY", None, asset_class="ETF"
        )

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
        assert exc_info.value.context.get("venue_unresolved") is True

    @pytest.mark.asyncio
    async def test_named_catalog_etf_single_run_sizes_whole_shares(self, synthetic_catalog: Path):
        """Story 5.2 AC1/AC2: a plain ETF (IVV.ARCA) run completes and fills in whole shares.

        Whole-share sizing is only observable through a real run — every order fill
        quantity must be a whole number (no fractional part), identical to a Stock.
        """
        from src.core.backtest_orchestrator import BacktestOrchestrator

        window_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2018, 3, 1, tzinfo=timezone.utc)

        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "IVV", "IVV.ARCA", asset_class="ETF"
        )

        load_result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="IVV",
            bar_type_spec="1-DAY-LAST",
            start=window_start,
            end=window_end,
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        request = _build_sma_request(symbol="IVV", start=window_start, end=window_end)
        orchestrator = BacktestOrchestrator()
        try:
            result, run_id = await orchestrator.execute(
                request, load_result.bars, load_result.instrument
            )
            assert result is not None
            assert result.final_balance > 0
            assert run_id is None  # persist=False

            assert orchestrator.engine is not None
            fills = orchestrator.engine.trader.generate_order_fills_report()
            assert not fills.empty, "expected at least one order fill to verify sizing"
            for qty in fills["quantity"].tolist():
                assert float(str(qty)).is_integer(), f"ETF fill not whole-share: {qty}"
                assert "." not in str(qty)
        finally:
            orchestrator.dispose()

    @pytest.mark.asyncio
    async def test_named_catalog_leveraged_etf_settles_as_ordinary_shares(
        self, synthetic_catalog: Path
    ):
        """Story 5.2 AC3: a leveraged ETF (TQQQ.NASDAQ) settles as ordinary whole shares.

        No leverage/inverse special-casing — TQQQ is a plain Equity (size_precision=0),
        sized by the identical whole-share branch as SPY/Stocks (not crypto/FX rules).
        """
        from nautilus_trader.model.instruments import Equity

        from src.core.backtest_orchestrator import BacktestOrchestrator

        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            "TQQQ", "TQQQ.NASDAQ", asset_class="ETF"
        )

        load_result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="TQQQ",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 3, 1, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        # Ordinary-shares settlement precondition — no crypto/FX fractional precision.
        assert isinstance(load_result.instrument, Equity)
        assert load_result.instrument.size_precision == 0

        request = _build_sma_request(
            symbol="TQQQ",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 3, 1, tzinfo=timezone.utc),
        )
        orchestrator = BacktestOrchestrator()
        try:
            result, run_id = await orchestrator.execute(
                request, load_result.bars, load_result.instrument
            )
            assert result is not None
            assert result.final_balance > 0
            assert run_id is None  # persist=False — no DB write (AC4)

            assert orchestrator.engine is not None
            fills = orchestrator.engine.trader.generate_order_fills_report()
            assert not fills.empty, "zigzag bars should trigger multiple crossovers"
            for qty in fills["quantity"].tolist():
                assert float(str(qty)).is_integer(), f"leveraged ETF fill not whole-share: {qty}"
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
