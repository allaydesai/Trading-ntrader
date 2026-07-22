"""Integration tests for named-catalog BacktestEngine flow (Story 3.1, Task 8).

Runs the full flow against a tmp_path-scoped ParquetDataCatalog with synthetic
bars. Requires --forked (Nautilus C/Rust extension isolation).
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
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
    persist: bool = False,
):
    from decimal import Decimal

    from src.models.backtest_request import BacktestRequest

    return BacktestRequest.from_cli_args(
        strategy="sma_crossover",
        symbol=symbol,
        start=start,
        end=end,
        bar_type_spec="1-DAY-LAST",
        persist=persist,
        starting_balance=Decimal("100000"),
        data_source="catalog",
        catalog_name=catalog_name,
        portfolio_value=Decimal("100000"),
        position_size_pct=Decimal("10.0"),
        fast_period=2,
        slow_period=5,
    )


def get_worker_id(request) -> str:
    """Get pytest-xdist worker ID or 'master' if running without xdist."""
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


def _test_schema_name(request) -> str:
    """Per-worker isolated schema name for the results DB round-trip (Story 5.4)."""
    return f"test_{get_worker_id(request)}".replace("-", "_")


@pytest.fixture
async def async_test_engine(request):
    """Async results-DB engine with per-worker schema isolation (Story 5.4 persistence).

    Creates the existing results-DB tables via ``Base.metadata.create_all`` in a
    throwaway schema — this is test scaffolding, NOT an Alembic migration or a
    schema change to the app DB (AC4).
    """
    from src.config import get_settings

    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    schema_name = _test_schema_name(request)

    engine = create_async_engine(async_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        await conn.execute(text(f"SET search_path TO {schema_name}"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
    await engine.dispose()


@pytest.fixture
async def async_session(async_test_engine, request):
    """Read-back session on the isolated test schema (separate from the write session)."""
    schema_name = _test_schema_name(request)
    session_maker = async_sessionmaker(
        async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        await session.execute(text(f"SET search_path TO {schema_name}"))
        yield session


def _persisted_columns(run) -> set[str]:
    """Non-None column names on a persisted BacktestRun, for ETF↔Stock parity comparison."""
    from src.db.models.backtest import BacktestRun

    return {c.name for c in BacktestRun.__table__.columns if getattr(run, c.name) is not None}


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
    async def test_named_catalog_multi_etf_run_no_venue_bleed(self, synthetic_catalog: Path):
        """Story 5.3 AC1/AC2/AC3: one run spans two ETFs on different venues, no bleed.

        IVV.ARCA + TQQQ.NASDAQ run through load_many_from_catalog → execute_multi in a
        SINGLE engine. Assert the run completes, both instruments fill, every fill is
        whole-share and settles on its OWN venue (an ARCA ticker never fills on NASDAQ),
        two distinct venues/accounts exist, and the two strategies carry distinct ids.
        """
        from src.core.backtest_orchestrator import BacktestOrchestrator
        from src.services.firstrate.backtest_loader import load_many_from_catalog

        window_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2018, 3, 1, tzinfo=timezone.utc)

        rows = {
            "IVV": _make_instrument_row("IVV", "IVV.ARCA", asset_class="ETF"),
            "TQQQ": _make_instrument_row("TQQQ", "TQQQ.NASDAQ", asset_class="ETF"),
        }
        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.side_effect = lambda _catalog, ticker: rows[ticker]

        load_results = await load_many_from_catalog(
            catalog_name="e2e-test",
            tickers=["IVV", "TQQQ"],
            bar_type_spec="1-DAY-LAST",
            start=window_start,
            end=window_end,
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )
        assert len(load_results) == 2
        instruments_data = [(r.bars, r.instrument) for r in load_results]

        request = _build_sma_request(symbol="IVV", start=window_start, end=window_end)
        orchestrator = BacktestOrchestrator()
        try:
            result, run_id = await orchestrator.execute_multi(request, instruments_data)
            assert result is not None
            assert result.final_balance > 0
            assert run_id is None  # multi path never persists (Story 5.4)

            # Two venues (ARCA + NASDAQ) → two accounts, each seeded with 100k. The
            # summary must report the PORTFOLIO AGGREGATE (~200k), not one venue's
            # account (~100k) — proving _aggregate_multi_venue_balance sums accounts.
            assert result.final_balance > 150_000, (
                f"multi-venue final_balance under-reported (one account?): {result.final_balance}"
            )

            assert orchestrator.engine is not None
            fills = orchestrator.engine.trader.generate_order_fills_report()
            assert not fills.empty, "expected fills for the multi-ETF run"

            instrument_ids = [str(i) for i in fills["instrument_id"].tolist()]
            venues_seen = {iid.rsplit(".", 1)[-1] for iid in instrument_ids}
            # Both ETFs traded, each on its OWN venue — no cross-instrument bleed.
            assert any(iid.startswith("IVV.ARCA") for iid in instrument_ids), instrument_ids
            assert any(iid.startswith("TQQQ.NASDAQ") for iid in instrument_ids), instrument_ids
            assert venues_seen == {"ARCA", "NASDAQ"}, venues_seen
            # No IVV fill on NASDAQ and no TQQQ fill on ARCA.
            for iid in instrument_ids:
                if iid.startswith("IVV"):
                    assert iid.endswith(".ARCA"), f"IVV bled to wrong venue: {iid}"
                if iid.startswith("TQQQ"):
                    assert iid.endswith(".NASDAQ"), f"TQQQ bled to wrong venue: {iid}"

            for qty in fills["quantity"].tolist():
                assert float(str(qty)).is_integer(), f"multi-ETF fill not whole-share: {qty}"
                assert "." not in str(qty)

            # One strategy instance per instrument, distinct ids (AC3, order_id_tag).
            strategy_ids = [str(s) for s in orchestrator.engine.trader.strategy_ids()]
            assert len(strategy_ids) == 2, strategy_ids
            assert len(set(strategy_ids)) == 2, f"strategy ids collided: {strategy_ids}"
        finally:
            orchestrator.dispose()

    @pytest.mark.asyncio
    async def test_named_catalog_multi_etf_same_venue_shares_one_account(
        self, synthetic_catalog: Path
    ):
        """Story 5.3 AC2 (dedup): two ETFs on the SAME venue share one account, added once.

        IVV.ARCA + SPY.ARCA both resolve to ARCA — the venue is added exactly once and
        the two instruments share a single account. The summary reports that one
        account (~100k), NOT a doubled aggregate, and only one venue appears.
        """
        from src.core.backtest_orchestrator import BacktestOrchestrator
        from src.services.firstrate.backtest_loader import load_many_from_catalog

        window_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2018, 3, 1, tzinfo=timezone.utc)

        rows = {
            "IVV": _make_instrument_row("IVV", "IVV.ARCA", asset_class="ETF"),
            "SPY": _make_instrument_row("SPY", "SPY.ARCA", asset_class="ETF"),
        }
        catalog_manager = CatalogManager(synthetic_catalog)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.side_effect = lambda _catalog, ticker: rows[ticker]

        load_results = await load_many_from_catalog(
            catalog_name="e2e-test",
            tickers=["IVV", "SPY"],
            bar_type_spec="1-DAY-LAST",
            start=window_start,
            end=window_end,
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )
        instruments_data = [(r.bars, r.instrument) for r in load_results]

        request = _build_sma_request(symbol="IVV", start=window_start, end=window_end)
        orchestrator = BacktestOrchestrator()
        try:
            result, run_id = await orchestrator.execute_multi(request, instruments_data)
            assert result is not None
            assert run_id is None
            # One shared ARCA account — NOT a two-account aggregate.
            assert 0 < result.final_balance < 150_000, result.final_balance

            assert orchestrator.engine is not None
            fills = orchestrator.engine.trader.generate_order_fills_report()
            if not fills.empty:
                venues = {str(i).rsplit(".", 1)[-1] for i in fills["instrument_id"].tolist()}
                assert venues <= {"ARCA"}, venues
            # Two strategies (one per instrument), distinct ids, but one venue/account.
            strategy_ids = [str(s) for s in orchestrator.engine.trader.strategy_ids()]
            assert len(set(strategy_ids)) == 2, strategy_ids
        finally:
            orchestrator.dispose()

    @pytest.mark.asyncio
    async def test_named_catalog_multi_etf_rejects_duplicate_instrument(
        self, synthetic_catalog: Path
    ):
        """Story 5.3: a duplicate instrument in the list is rejected (no double-add)."""
        from src.core.backtest_orchestrator import BacktestOrchestrator
        from src.services.firstrate.backtest_loader import load_from_catalog

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
        dup = [
            (load_result.bars, load_result.instrument),
            (load_result.bars, load_result.instrument),
        ]
        request = _build_sma_request(symbol="IVV", start=window_start, end=window_end)
        orchestrator = BacktestOrchestrator()
        try:
            with pytest.raises(ValueError, match="Duplicate instrument"):
                await orchestrator.execute_multi(request, dup)
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


@pytest.mark.integration
class TestNamedCatalogEtfPersistence:
    """Story 5.4: an ETF run persists via the existing DB path and is web-viewable.

    Verification/lock-in: the persist + display path is asset-class-agnostic (an ETF is
    erased to a plain Equity + symbol string at load time), so an ETF run is recorded and
    displayed identically to a Stock. No production code, no migration, no schema change.
    """

    def _write_session_ctx(self, engine, schema_name):
        """Build a get_session replacement yielding a session on the isolated test schema.

        ``BacktestOrchestrator._persist_results`` opens its OWN session via
        ``src.db.session.get_session`` and commits internally; monkeypatching that name to
        this factory routes the write to the test schema (never the app DB).
        """
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def _ctx():
            async with maker() as session:
                await session.execute(text(f"SET search_path TO {schema_name}"))
                yield session

        return _ctx

    async def _persist_run(self, catalog_base, symbol, nautilus_id, asset_class, start, end):
        """Run + persist a single instrument through the existing path; return its run_id."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        catalog_manager = CatalogManager(catalog_base)
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            symbol, nautilus_id, asset_class=asset_class
        )
        load_result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker=symbol,
            bar_type_spec="1-DAY-LAST",
            start=start,
            end=end,
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )
        request = _build_sma_request(symbol=symbol, start=start, end=end, persist=True)
        orchestrator = BacktestOrchestrator()
        try:
            _result, run_id = await orchestrator.execute(
                request, load_result.bars, load_result.instrument
            )
            return run_id
        finally:
            orchestrator.dispose()

    @pytest.mark.asyncio
    async def test_named_catalog_etf_run_persists_results(
        self, synthetic_catalog, async_test_engine, async_session, request, monkeypatch
    ):
        """AC1/AC3: a real ETF run persists a backtest_runs + performance_metrics record,
        identical in shape to a Stock run (no asset_class column anywhere)."""
        from src.db.repositories.backtest_repository import BacktestRepository

        schema_name = _test_schema_name(request)
        monkeypatch.setattr(
            "src.core.backtest_orchestrator.get_session",
            self._write_session_ctx(async_test_engine, schema_name),
        )

        etf_start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        etf_end = datetime(2018, 3, 1, tzinfo=timezone.utc)
        etf_run_id = await self._persist_run(
            synthetic_catalog, "IVV", "IVV.ARCA", "ETF", etf_start, etf_end
        )
        assert etf_run_id is not None  # persist=True → run_id assigned

        # Read-back is the real proof (persist swallows its own exceptions).
        etf_run = await BacktestRepository(async_session).find_by_run_id(etf_run_id)
        assert etf_run is not None, "ETF run was not persisted to the results DB"
        assert etf_run.instrument_symbol == "IVV"
        assert etf_run.execution_status == "success"
        assert etf_run.data_source == "catalog:e2e-test"
        assert etf_run.strategy_type == "sma_crossover"
        assert etf_run.metrics is not None  # 1:1 performance_metrics row written
        assert not hasattr(etf_run, "asset_class")
        # AC3: the "no asset-class column" guarantee extends to performance_metrics and
        # trades too — a schema-level tripwire (deterministic, independent of how many
        # trade rows a given run produces).
        from src.db.models.backtest import PerformanceMetrics
        from src.db.models.trade import Trade

        assert "asset_class" not in {c.name for c in PerformanceMetrics.__table__.columns}
        assert "asset_class" not in {c.name for c in Trade.__table__.columns}

        # Parity: a Stock persists the identical record shape via the identical path (AC3).
        stock_run_id = await self._persist_run(
            synthetic_catalog,
            "AAPL",
            "AAPL.NASDAQ",
            "STOCK",
            datetime(2018, 1, 1, tzinfo=timezone.utc),
            datetime(2018, 1, 11, tzinfo=timezone.utc),
        )
        assert stock_run_id is not None
        stock_run = await BacktestRepository(async_session).find_by_run_id(stock_run_id)
        assert stock_run is not None
        assert not hasattr(stock_run, "asset_class")
        assert _persisted_columns(etf_run) == _persisted_columns(stock_run)

    @pytest.mark.asyncio
    async def test_persisted_etf_run_is_viewable_in_web_read_path(
        self, synthetic_catalog, async_test_engine, async_session, request, monkeypatch
    ):
        """AC2: the persisted ETF run is viewable via the exact web read path
        (BacktestQueryService.get_backtest_by_id → to_detail_view), no asset-class branch."""
        from src.api.models.backtest_detail import to_detail_view
        from src.db.repositories.backtest_repository import BacktestRepository
        from src.services.backtest_query import BacktestQueryService

        schema_name = _test_schema_name(request)
        monkeypatch.setattr(
            "src.core.backtest_orchestrator.get_session",
            self._write_session_ctx(async_test_engine, schema_name),
        )

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 3, 1, tzinfo=timezone.utc)
        run_id = await self._persist_run(synthetic_catalog, "IVV", "IVV.ARCA", "ETF", start, end)
        assert run_id is not None

        # Exactly what the FastAPI detail route reads.
        service = BacktestQueryService(BacktestRepository(async_session))
        run = await service.get_backtest_by_id(run_id)
        assert run is not None
        assert run.instrument_symbol == "IVV"
        assert run.metrics is not None  # eager-loaded — no lazy access in the async test

        view = to_detail_view(run)
        assert view.configuration.instrument_symbol == "IVV"  # ticker renders plainly (not kraken)
        assert view.execution_status == "success"
