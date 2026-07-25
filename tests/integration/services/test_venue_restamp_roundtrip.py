"""Integration proof that a re-stamped partition loads through the production path.

The unit and component tiers check the rewrite mechanically. This tier answers the
only question that actually matters: after a venue correction, does
``backtest_loader.load_from_catalog`` return the bars, under the new identity, via
the real ``ParquetDataCatalog``?

The control test is the important half. ``load_from_catalog`` already contains a
defensive check that the venue on the first bar matches the venue the DB claims,
raising ``ValueError: Venue mismatch``. That makes it a ready-made oracle: a
rename-without-restamp — the exact shape of a half-finished implementation — must
fail there. Pinning that guard means this suite cannot silently stop testing
anything.

Requires --forked (Nautilus C/Rust extension isolation).
"""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow.parquet as pq
import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from src.db.models.catalog_instrument import CatalogInstrument
from src.services.firstrate.backtest_loader import load_from_catalog
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.venue_restamp import execute_action, execute_plan
from src.services.firstrate.venue_restamp_plan import build_restamp_plan

pytestmark = pytest.mark.integration

CATALOG = "restamp-test"
_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
_END = datetime(2020, 3, 1, tzinfo=timezone.utc)
_BAR_COUNT = 30


def _bars(instrument_id: str, spec: str = "1-DAY-LAST", count: int = _BAR_COUNT) -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument_id}-{spec}-EXTERNAL")
    out = []
    for i in range(count):
        ts = int((_START + timedelta(days=i)).timestamp() * 1e9)
        price = 100 + i
        out.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{price}.00"),
                high=Price.from_str(f"{price + 2}.00"),
                low=Price.from_str(f"{price - 1}.00"),
                close=Price.from_str(f"{price + 1}.00"),
                volume=Quantity.from_str(f"{1000 + i}"),
                ts_event=ts,
                ts_init=ts,
            )
        )
    return out


def _instrument_row(ticker: str, nautilus_id: str) -> CatalogInstrument:
    row = MagicMock(spec=CatalogInstrument)
    row.ticker = ticker
    row.nautilus_id = nautilus_id
    row.catalog_name = CATALOG
    row.asset_class = "ETF"
    row.date_range_start = _START
    row.date_range_end = _END
    return row


def _metadata_service(ticker: str, nautilus_id: str):
    service = MagicMock()
    service.get_instrument_sync.return_value = _instrument_row(ticker, nautilus_id)
    return service


@pytest.fixture
def catalog_base(tmp_path: Path) -> Path:
    """A catalog holding AAA under the wrong venue plus an untouched control."""
    base = tmp_path / "catalogs"
    catalog_dir = base / CATALOG
    catalog_dir.mkdir(parents=True)
    cat = ParquetDataCatalog(path=str(catalog_dir), fs_protocol="file")
    cat.write_data(_bars("AAA.AMEX"))
    cat.write_data(_bars("AAA.AMEX", spec="1-HOUR-LAST"))
    cat.write_data(_bars("KEEP.NASDAQ"))
    return base


async def _load(base: Path, ticker: str, nautilus_id: str, spec: str = "1-DAY-LAST"):
    return await load_from_catalog(
        catalog_name=CATALOG,
        ticker=ticker,
        bar_type_spec=spec,
        start=_START,
        end=_END,
        catalog_manager=CatalogManager(base),
        metadata_service=_metadata_service(ticker, nautilus_id),
    )


@pytest.mark.asyncio
class TestRestampRoundTrip:
    """The full loop: plan, execute, load."""

    async def test_restamped_partition_loads_under_the_new_identity(self, catalog_base):
        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        assert plan.is_safe
        outcome = execute_plan(plan, workers=2, deep_verify=True)
        assert outcome.ok, outcome.failed

        result = await _load(catalog_base, "AAA", "AAA.ARCA")

        assert len(result.bars) == _BAR_COUNT
        assert str(result.bars[0].bar_type) == "AAA.ARCA-1-DAY-LAST-EXTERNAL"
        assert str(result.instrument.id.venue) == "ARCA"

    async def test_every_timeframe_moves_together(self, catalog_base):
        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        execute_plan(plan, workers=2)

        hourly = await _load(catalog_base, "AAA", "AAA.ARCA", spec="1-HOUR-LAST")

        assert len(hourly.bars) == _BAR_COUNT
        assert str(hourly.bars[0].bar_type) == "AAA.ARCA-1-HOUR-LAST-EXTERNAL"

    async def test_prices_survive_the_rewrite_exactly(self, catalog_base):
        """Bit-identical bars is the reason to re-stamp instead of re-importing."""
        before = await _load(catalog_base, "AAA", "AAA.AMEX")
        before_ohlc = [(b.open, b.high, b.low, b.close, b.volume) for b in before.bars]

        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        execute_plan(plan, workers=2)

        after = await _load(catalog_base, "AAA", "AAA.ARCA")
        after_ohlc = [(b.open, b.high, b.low, b.close, b.volume) for b in after.bars]

        assert after_ohlc == before_ohlc
        assert [b.ts_event for b in after.bars] == [b.ts_event for b in before.bars]

    async def test_old_identity_no_longer_resolves(self, catalog_base):
        """The stale partition is gone, not merely shadowed."""
        from src.services.exceptions import DataNotFoundError

        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        execute_plan(plan, workers=2)

        with pytest.raises(DataNotFoundError):
            await _load(catalog_base, "AAA", "AAA.AMEX")

    async def test_no_op_ticker_is_untouched_byte_for_byte(self, catalog_base):
        keep_dir = catalog_base / CATALOG / "data" / "bar" / "KEEP.NASDAQ-1-DAY-LAST-EXTERNAL"
        before = {p.name: p.read_bytes() for p in keep_dir.glob("*.parquet")}

        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        execute_plan(plan, workers=2)

        after = {p.name: p.read_bytes() for p in keep_dir.glob("*.parquet")}
        assert after == before


@pytest.mark.asyncio
class TestOracle:
    """Pin the guard this suite relies on, so it cannot silently stop working."""

    async def test_rename_without_restamp_raises_venue_mismatch(self, catalog_base):
        """The exact failure mode of a half-finished implementation.

        Move the directory but leave the parquet footer claiming the old venue.
        ``load_from_catalog`` must reject it — that rejection is what makes every
        other test in this file meaningful.
        """
        bar_root = catalog_base / CATALOG / "data" / "bar"
        shutil.move(
            str(bar_root / "AAA.AMEX-1-DAY-LAST-EXTERNAL"),
            str(bar_root / "AAA.ARCA-1-DAY-LAST-EXTERNAL"),
        )

        with pytest.raises(ValueError, match="Venue mismatch"):
            await _load(catalog_base, "AAA", "AAA.ARCA")

    async def test_restamped_partition_passes_the_same_guard(self, catalog_base):
        """Control for the above: the real path clears the guard the rename trips."""
        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        execute_plan(plan, workers=1)

        result = await _load(catalog_base, "AAA", "AAA.ARCA")
        assert str(result.instrument.id.venue) == "ARCA"


@pytest.mark.asyncio
class TestResumeSafety:
    """Interruption must never cost bars."""

    async def test_interrupted_run_recovers_and_still_loads(self, catalog_base):
        bar_root = catalog_base / CATALOG / "data" / "bar"
        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        action = next(a for a in plan.actions if a.spec == "1-DAY-LAST")

        # Simulate a crash mid-copy: destination exists, half-written, source intact.
        action.dst_dir.mkdir(parents=True)
        (action.dst_dir / "partial.parquet").write_bytes(b"garbage")

        ok, detail = execute_action(action)
        assert ok, detail

        result = await _load(catalog_base, "AAA", "AAA.ARCA")
        assert len(result.bars) == _BAR_COUNT
        assert not (bar_root / "AAA.AMEX-1-DAY-LAST-EXTERNAL").exists()

    async def test_rerunning_a_completed_plan_is_a_noop(self, catalog_base):
        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        first = execute_plan(plan, workers=2)
        second = execute_plan(plan, workers=2)

        assert first.completed == 2
        assert second.completed == 0
        assert second.skipped == 2
        assert second.ok

    async def test_a_fresh_plan_after_completion_has_nothing_to_do(self, catalog_base):
        """The operator's confirmation check: re-planning shows zero remaining."""
        execute_plan(
            build_restamp_plan(
                catalog=CATALOG,
                catalog_root=catalog_base / CATALOG,
                target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
            ),
            workers=2,
        )

        replan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )

        assert replan.actions == ()
        assert replan.is_safe
        assert replan.no_ops == 3


@pytest.mark.asyncio
class TestFooterIntegrity:
    """What the catalog reader actually consumes."""

    async def test_both_footer_keys_match_the_directory(self, catalog_base):
        plan = build_restamp_plan(
            catalog=CATALOG,
            catalog_root=catalog_base / CATALOG,
            target_venues={"AAA": "ARCA", "KEEP": "NASDAQ"},
        )
        execute_plan(plan, workers=2)

        directory = catalog_base / CATALOG / "data" / "bar" / "AAA.ARCA-1-DAY-LAST-EXTERNAL"
        parquet = next(directory.glob("*.parquet"))
        metadata = pq.ParquetFile(parquet).schema_arrow.metadata

        assert metadata[b"bar_type"] == b"AAA.ARCA-1-DAY-LAST-EXTERNAL"
        assert metadata[b"instrument_id"] == b"AAA.ARCA"
