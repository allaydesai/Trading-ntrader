"""Component tests for the named-catalog-backed backtest loader (Story 3.1)."""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.db.models.catalog_instrument import CatalogInstrument
from src.services.exceptions import DataNotFoundError


def _make_instrument_row(
    *,
    ticker: str = "AAPL",
    nautilus_id: str | None = "AAPL.NASDAQ",
    asset_class: str = "STOCK",
    date_range_start: datetime = datetime(2018, 1, 1, tzinfo=timezone.utc),
    date_range_end: datetime = datetime(2018, 12, 31, tzinfo=timezone.utc),
) -> CatalogInstrument:
    """Build a CatalogInstrument spec-backed mock."""
    row = MagicMock(spec=CatalogInstrument)
    row.ticker = ticker
    row.nautilus_id = nautilus_id
    row.asset_class = asset_class
    row.date_range_start = date_range_start
    row.date_range_end = date_range_end
    return row


class _StringVenue:
    """Minimal stand-in with a class-level ``__str__``.

    MagicMock delegates ``str()`` to its class-level ``__str__``, so assigning
    ``__str__`` on a MagicMock *instance* has no effect (Python looks up dunders
    on ``type(obj)``, not the instance dict). This tiny class guarantees
    ``str(bar.bar_type.instrument_id.venue)`` returns the expected venue value.
    """

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


def _make_bar_for(nautilus_id: str, precision: int = 2):
    """Build a mock Bar whose bar_type.instrument_id.venue stringifies to the ID's venue."""
    venue_str = nautilus_id.rsplit(".", 1)[-1]
    bar = MagicMock()
    bar.bar_type.instrument_id.venue = _StringVenue(venue_str)
    bar.open.precision = precision
    return bar


@pytest.mark.component
class TestLoadFromCatalog:
    """Behavioral tests for load_from_catalog."""

    @pytest.mark.asyncio
    async def test_resolves_catalog_and_reads_bars(self):
        """Given catalog_name + ticker, resolve nautilus_id, build BarType, call catalog.bars()."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog = MagicMock()
        mock_bars = [_make_bar_for("AAPL.NASDAQ"), _make_bar_for("AAPL.NASDAQ")]
        catalog.bars.return_value = mock_bars

        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.return_value = catalog

        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row()

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 6, 30, tzinfo=timezone.utc)

        result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="AAPL",
            bar_type_spec="1-MINUTE-LAST",
            start=start,
            end=end,
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        catalog_manager.resolve_catalog.assert_called_once_with("e2e-test")
        metadata_service.get_instrument_sync.assert_called_once_with("e2e-test", "AAPL")

        kwargs = catalog.bars.call_args.kwargs
        assert kwargs["bar_types"] == ["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"]
        assert kwargs["start"] == start
        assert kwargs["end"] == end

        assert result.bars == mock_bars
        assert result.data_source_used == "Catalog: e2e-test"
        assert result.instrument.id.symbol.value == "AAPL"
        assert str(result.instrument.id.venue) == "NASDAQ"

    @pytest.mark.asyncio
    async def test_missing_ticker_raises_data_not_found(self):
        """When MetadataService returns None, raise DataNotFoundError with catalog context."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog_manager = MagicMock()
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = None

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(DataNotFoundError) as exc_info:
            await load_from_catalog(
                catalog_name="e2e-test",
                ticker="SPY",
                bar_type_spec="1-MINUTE-LAST",
                start=start,
                end=end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        assert exc_info.value.instrument_id == "SPY"
        assert exc_info.value.context.get("missing_from_catalog") == "e2e-test"
        # Do NOT reach the catalog if the ticker isn't in the DB
        catalog_manager.resolve_catalog.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_window_raises_data_not_found_with_metadata_range(self):
        """When catalog.bars() returns [], raise with both metadata and requested ranges."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog = MagicMock()
        catalog.bars.return_value = []

        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.return_value = catalog

        metadata_range = (
            datetime(2018, 1, 1, tzinfo=timezone.utc),
            datetime(2018, 12, 31, tzinfo=timezone.utc),
        )
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            date_range_start=metadata_range[0],
            date_range_end=metadata_range[1],
        )

        requested_start = datetime(2027, 1, 1, tzinfo=timezone.utc)
        requested_end = datetime(2027, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(DataNotFoundError) as exc_info:
            await load_from_catalog(
                catalog_name="e2e-test",
                ticker="AAPL",
                bar_type_spec="1-MINUTE-LAST",
                start=requested_start,
                end=requested_end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        assert exc_info.value.instrument_id == "AAPL"
        ctx = exc_info.value.context
        assert ctx.get("catalog") == "e2e-test"
        assert ctx.get("metadata_range") == metadata_range
        assert "2027" in str(exc_info.value)
        assert "2018" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unknown_catalog_raises_user_error_with_available_list(self):
        """FileNotFoundError from CatalogManager is caught and re-raised as ValueError."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.side_effect = FileNotFoundError(
            "Catalog 'does-not-exist' not found at /tmp/catalogs/does-not-exist. "
            "Available: ['e2e-test', 'firstrate']"
        )
        catalog_manager.list_catalogs.return_value = ["e2e-test", "firstrate"]

        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row()

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(ValueError) as exc_info:
            await load_from_catalog(
                catalog_name="does-not-exist",
                ticker="AAPL",
                bar_type_spec="1-MINUTE-LAST",
                start=start,
                end=end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        msg = str(exc_info.value)
        assert "does-not-exist" in msg
        assert "e2e-test" in msg
        assert "firstrate" in msg
        assert "Available" in msg

    @pytest.mark.asyncio
    async def test_instrument_venue_parsed_safely_for_dot_tickers(self):
        """BRK.B and similar multi-dot tickers must parse via InstrumentId.from_str."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog = MagicMock()
        catalog.bars.return_value = [_make_bar_for("BRK.B.NYSE")]

        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.return_value = catalog

        metadata_service = MagicMock()
        # BRK.B on NYSE — nautilus_id = "BRK.B.NYSE"
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            ticker="BRK.B", nautilus_id="BRK.B.NYSE"
        )

        result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="BRK.B",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 6, 30, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        assert str(result.instrument.id.venue) == "NYSE"

    @pytest.mark.asyncio
    async def test_returns_data_load_result_shape(self):
        """The public entry point returns DataLoadResult for caller compatibility."""
        from src.cli.commands._backtest_helpers import DataLoadResult
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog = MagicMock()
        catalog.bars.return_value = [_make_bar_for("AAPL.NASDAQ")]

        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.return_value = catalog

        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row()

        result = await load_from_catalog(
            catalog_name="e2e-test",
            ticker="AAPL",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 6, 30, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        assert isinstance(result, DataLoadResult)


@pytest.mark.component
class TestEtfRoutingNoAdapter:
    """Story 5.1 AC1/AC3: ETFs are served by the identical Equity path as Stocks.

    The loader never branches on ``asset_class`` — an ETF flows through the same
    ``build_equity`` synthesis, proving NFR16 "no runtime adapter". The synthesised
    instrument is equity-shaped (``Equity``, ``lot_size == 1``, USD), the precondition
    Story 5.2 whole-share sizing depends on.
    """

    @pytest.mark.asyncio
    async def test_etf_served_via_identical_equity_path(self):
        """An ETF row (asset_class='ETF') resolves to a Nautilus Equity on ARCA."""
        from nautilus_trader.model.currencies import USD
        from nautilus_trader.model.instruments import Equity

        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog = MagicMock()
        catalog.bars.return_value = [_make_bar_for("SPY.ARCA"), _make_bar_for("SPY.ARCA")]

        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.return_value = catalog

        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            ticker="SPY", nautilus_id="SPY.ARCA", asset_class="ETF"
        )

        result = await load_from_catalog(
            catalog_name="etf-full",
            ticker="SPY",
            bar_type_spec="1-DAY-LAST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 6, 30, tzinfo=timezone.utc),
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )

        # Same code path as Stocks: bar_type built off nautilus_id, no ETF branch.
        assert catalog.bars.call_args.kwargs["bar_types"] == ["SPY.ARCA-1-DAY-LAST-EXTERNAL"]

        instrument = result.instrument
        assert isinstance(instrument, Equity), "ETF must synthesise a whole-share Equity"
        assert instrument.id.symbol.value == "SPY"
        assert str(instrument.id.venue) == "ARCA"
        # Equity-shape precondition for Story 5.2 whole-share sizing.
        assert int(instrument.lot_size) == 1
        assert instrument.quote_currency == USD

    @pytest.mark.asyncio
    async def test_unresolved_venue_etf_fails_fast_before_catalog_read(self):
        """Story 5.1 AC2: an unresolved-venue ETF (nautilus_id None) never enters a run."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog = MagicMock()
        catalog_manager = MagicMock()
        catalog_manager.resolve_catalog.return_value = catalog

        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            ticker="SPY", nautilus_id=None, asset_class="ETF"
        )

        with pytest.raises(DataNotFoundError) as exc_info:
            await load_from_catalog(
                catalog_name="etf-full",
                ticker="SPY",
                bar_type_spec="1-DAY-LAST",
                start=datetime(2018, 1, 1, tzinfo=timezone.utc),
                end=datetime(2018, 6, 30, tzinfo=timezone.utc),
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        assert exc_info.value.instrument_id == "SPY"
        assert exc_info.value.context.get("venue_unresolved") is True
        assert exc_info.value.context.get("catalog") == "etf-full"
        msg = str(exc_info.value).lower()
        assert "unresolved venue" in msg and "non-backtestable" in msg
        # Never touch the filesystem / never enter a run for an excluded instrument.
        catalog_manager.resolve_catalog.assert_not_called()
        catalog.bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_blank_nautilus_id_also_fails_fast(self):
        """A blank (empty-string) identity is treated as unresolved, never admitted."""
        from src.services.firstrate.backtest_loader import load_from_catalog

        catalog_manager = MagicMock()
        metadata_service = MagicMock()
        metadata_service.get_instrument_sync.return_value = _make_instrument_row(
            ticker="SPY", nautilus_id="", asset_class="ETF"
        )

        with pytest.raises(DataNotFoundError) as exc_info:
            await load_from_catalog(
                catalog_name="etf-full",
                ticker="SPY",
                bar_type_spec="1-DAY-LAST",
                start=datetime(2018, 1, 1, tzinfo=timezone.utc),
                end=datetime(2018, 6, 30, tzinfo=timezone.utc),
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

        assert exc_info.value.context.get("venue_unresolved") is True
        catalog_manager.resolve_catalog.assert_not_called()


@pytest.mark.component
@pytest.mark.skipif(
    os.environ.get("E2E_CATALOG_AVAILABLE") != "1",
    reason="Requires the 21M-bar e2e-test catalog on the local machine.",
)
class TestReferenceParity:
    """AC #12: new path vs legacy path must produce byte-identical bar lists.

    This test is local-only (21M bars not in CI). Guard via env var.
    """

    @pytest.mark.asyncio
    async def test_aapl_2018_1min_identical_via_both_paths(self):
        """AAPL 2018 1-MINUTE must be byte-identical between named and default catalog."""
        import os as _os

        from src.services.data_catalog import DataCatalogService
        from src.services.firstrate.backtest_loader import load_from_catalog
        from src.services.firstrate.catalog_manager import CatalogManager
        from src.services.firstrate.metadata_service import MetadataService

        base_path = _os.environ.get("CATALOG_BASE_PATH")
        if not base_path:
            pytest.skip("CATALOG_BASE_PATH not set — cannot locate named catalog")

        legacy_path = _os.environ.get("NAUTILUS_PATH")
        if not legacy_path:
            pytest.skip("NAUTILUS_PATH not set — cannot run legacy comparison")

        from pathlib import Path

        from src.db.repositories.catalog_instrument_repository import (
            SyncCatalogInstrumentRepository,
        )
        from src.db.session_sync import get_sync_session_maker

        session_maker = get_sync_session_maker()
        if session_maker is None:
            pytest.skip("Database not configured — cannot resolve metadata")

        session = session_maker()
        try:
            sync_repo = SyncCatalogInstrumentRepository(session)
            metadata_service = MetadataService(sync_repo=sync_repo)
            catalog_manager = CatalogManager(Path(base_path))

            start = datetime(2018, 1, 1, tzinfo=timezone.utc)
            end = datetime(2018, 12, 31, 23, 59, tzinfo=timezone.utc)

            new_result = await load_from_catalog(
                catalog_name="e2e-test",
                ticker="AAPL",
                bar_type_spec="1-MINUTE-LAST",
                start=start,
                end=end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )

            legacy_service = DataCatalogService(catalog_path=legacy_path)
            legacy_bars = await legacy_service.fetch_or_load(
                instrument_id="AAPL.NASDAQ",
                start=start,
                end=end,
                bar_type_spec="1-MINUTE-LAST",
                correlation_id="story-3-1-parity-test",
            )

            assert len(new_result.bars) == len(legacy_bars), (
                f"Bar count mismatch: new={len(new_result.bars)} legacy={len(legacy_bars)}"
            )
            for i, (a, b) in enumerate(zip(new_result.bars, legacy_bars)):
                assert a.ts_init == b.ts_init, f"ts_init mismatch at index {i}"
                assert a.close == b.close, f"close mismatch at index {i}"

            # Evidence capture for Story 3.3 hand-off (Task 7.2)
            evidence_dir = Path("/tmp/story-3-1-evidence")
            evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence_path = evidence_dir / "parity_aapl_2018_1min.txt"
            with open(evidence_path, "w") as f:
                f.write("Story 3.1 parity check — AAPL 2018 1-MINUTE\n")
                f.write(f"new_bars={len(new_result.bars)} legacy_bars={len(legacy_bars)}\n")
                f.write(
                    f"first ts_init: new={new_result.bars[0].ts_init} "
                    f"legacy={legacy_bars[0].ts_init}\n"
                )
                f.write(
                    f"first close: new={new_result.bars[0].close} legacy={legacy_bars[0].close}\n"
                )
                f.write(
                    f"last ts_init: new={new_result.bars[-1].ts_init} "
                    f"legacy={legacy_bars[-1].ts_init}\n"
                )
                f.write(
                    f"last close: new={new_result.bars[-1].close} legacy={legacy_bars[-1].close}\n"
                )
        finally:
            session.close()
