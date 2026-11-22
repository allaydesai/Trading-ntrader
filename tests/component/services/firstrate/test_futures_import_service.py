"""Component tests for the streaming futures import service (futures M2).

Runs against a real ``ParquetDataCatalog`` in ``tmp_path`` and an in-memory stand-in for
the ``catalog_instruments`` metadata service.
"""

from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.futures_import_service import (
    CONTRACT_KIND,
    ROLL_RULE,
    FuturesImportService,
)

pytestmark = pytest.mark.component

CATALOG = "firstrate-futures"
ES_1MIN = "ES.CME-1-MINUTE-LAST-EXTERNAL"


class FakeMetadataService:
    """In-memory ``get_instrument_sync`` / ``upsert_instrument_sync``."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], CatalogInstrument] = {}

    def get_instrument_sync(self, catalog_name: str, ticker: str):
        return self.rows.get((catalog_name, ticker))

    def upsert_instrument_sync(self, instrument: CatalogInstrument):
        self.rows[(instrument.catalog_name, instrument.ticker)] = instrument
        return instrument


def _minute_rows(count: int, start_hour: int = 10) -> list[str]:
    rows = []
    for i in range(count):
        hour, minute = divmod(i, 60)
        price = Decimal("5918.9") + Decimal(i) / 100  # alternates 1 and 2 decimals
        rows.append(
            f"2025-06-02 {start_hour + hour:02d}:{minute:02d}:00,{price},{price + 1},"
            f"{price - 1},{price},{i + 1}"
        )
    return rows


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    src = tmp_path / "raw"
    src.mkdir()
    (src / "ES_full_1min_continuous_ratio_adjusted.txt").write_text(
        "\n".join(_minute_rows(25)) + "\n"
    )
    (src / "ES_full_1day_continuous_ratio_adjusted.txt").write_text(
        "2025-06-02,5900.5,5950,5890.25,5940.75,1000,2000\n"
        "2025-06-03,5940.75,5960,5930,5955.5,1100,2100\n"
    )
    return src


@pytest.fixture()
def catalog_dir(tmp_path: Path) -> Path:
    base = tmp_path / "catalogs"
    (base / CATALOG).mkdir(parents=True)
    return base


@pytest.fixture()
def metadata() -> FakeMetadataService:
    return FakeMetadataService()


def _service(catalog_dir: Path, metadata, chunk_rows: int = 10) -> FuturesImportService:
    return FuturesImportService(CatalogManager(catalog_dir), metadata, chunk_rows=chunk_rows)


def _run(service, source: Path, timeframe: str = "1-MINUTE-LAST"):
    return service.import_directory(source, CATALOG, AssetClass.FUTURES, timeframe)


def _parts(catalog_dir: Path, bar_type: str = ES_1MIN) -> list[Path]:
    return sorted((catalog_dir / CATALOG / "data" / "bar" / bar_type).glob("*.parquet"))


def _read_all(catalog_dir: Path, bar_type: str = ES_1MIN) -> list[Bar]:
    return ParquetDataCatalog(str(catalog_dir / CATALOG)).bars(bar_types=[bar_type])


class TestFirstImport:
    def test_streams_chunks_into_parquet_parts(self, source, catalog_dir, metadata):
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "success", result.error
        assert result.outcome == "new"
        assert result.row_count == 25
        assert len(_parts(catalog_dir)) == 3
        assert sum(pq.read_metadata(p).num_rows for p in _parts(catalog_dir)) == 25

    def test_every_bar_has_the_root_precision(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        bars = _read_all(catalog_dir)
        assert len(bars) == 25
        assert {b.open.precision for b in bars} == {2}
        assert str(bars[1].open) == "5918.91"
        assert str(bars[10].open) == "5919.00"

    def test_catalog_row_populated_from_spec(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        row = metadata.rows[(CATALOG, "ES")]
        assert row.asset_class == "FUTURES"
        assert row.nautilus_id == "ES.CME"
        assert row.exchange == "CME"
        assert row.name == "E-mini S&P 500"
        assert row.multiplier == Decimal("50")
        assert row.tick_size == Decimal("0.25")
        assert row.currency == "USD"
        assert row.contract_kind == CONTRACT_KIND == "continuous"
        assert row.roll_rule == ROLL_RULE == "firstrate-ratio-adjusted"
        assert row.expiry is None
        assert row.bar_count_minute == 25
        assert row.date_range_start_minute is not None
        assert row.date_range_end_minute > row.date_range_start_minute

    def test_daily_seven_column_file(self, source, catalog_dir, metadata):
        [result] = _run(_service(catalog_dir, metadata), source, "1-DAY-LAST")
        assert result.status == "success", result.error
        assert result.row_count == 2
        assert metadata.rows[(CATALOG, "ES")].bar_count_daily == 2

    def test_discovers_only_requested_timeframe(self, source, catalog_dir, metadata):
        results = _run(_service(catalog_dir, metadata), source, "1-HOUR-LAST")
        assert results == []


class TestReRun:
    def test_second_run_is_skipped(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.status == "skipped"
        assert len(_parts(catalog_dir)) == 3

    def test_daily_rerun_is_skipped(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source, "1-DAY-LAST")
        [again] = _run(_service(catalog_dir, metadata), source, "1-DAY-LAST")
        assert again.status == "skipped"

    def test_grown_source_overwrites_without_duplicates(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        path = source / "ES_full_1min_continuous_ratio_adjusted.txt"
        path.write_text("\n".join(_minute_rows(40)) + "\n")
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 40
        assert metadata.rows[(CATALOG, "ES")].bar_count_minute == 40

    def test_partial_parquet_forces_reimport(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        _parts(catalog_dir)[-1].unlink()  # e.g. a crash after the delete, mid-write
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 25


class TestInterruptedWrites:
    def test_interrupted_replacement_with_matching_count_is_reimported(
        self, source, catalog_dir, metadata, monkeypatch
    ):
        from src.services.firstrate import futures_import_service as svc

        _run(_service(catalog_dir, metadata), source)
        real = svc.verify_series

        def crash(*args, **kwargs):  # parts fully rewritten, process dies before finishing
            raise KeyboardInterrupt

        monkeypatch.setattr(svc, "verify_series", crash)
        _parts(catalog_dir)[-1].unlink()  # forces a reimport of an otherwise complete series
        with pytest.raises(KeyboardInterrupt):
            _run(_service(catalog_dir, metadata), source)
        monkeypatch.setattr(svc, "verify_series", real)
        assert sum(pq.read_metadata(p).num_rows for p in _parts(catalog_dir)) == 25
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"

    def test_marker_cleared_after_success(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        bar_dir = catalog_dir / CATALOG / "data" / "bar" / ES_1MIN
        assert not (bar_dir / ".import-incomplete").exists()
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.status == "skipped"

    def test_unreadable_footer_forces_reimport(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        _parts(catalog_dir)[0].write_bytes(b"not parquet")
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.status == "success"
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 25


class TestCoverage:
    def test_shared_end_follows_a_regressed_source(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        path = source / "ES_full_1min_continuous_ratio_adjusted.txt"
        path.write_text("\n".join(_minute_rows(10)) + "\n")
        _run(_service(catalog_dir, metadata), source)
        row = metadata.rows[(CATALOG, "ES")]
        assert row.date_range_end == row.date_range_end_minute
        assert row.bar_count_minute == 10

    def test_shared_end_keeps_a_later_timeframe(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source, "1-DAY-LAST")
        _run(_service(catalog_dir, metadata), source)
        row = metadata.rows[(CATALOG, "ES")]
        assert row.date_range_end == max(row.date_range_end_daily, row.date_range_end_minute)


class TestFailures:
    def test_unknown_root_fails_loudly(self, source, catalog_dir, metadata):
        (source / "XX_full_1min_continuous_ratio_adjusted.txt").write_text(
            "2025-06-02 10:00:00,1,2,1,2,1\n"
        )
        results = {r.ticker: r for r in _run(_service(catalog_dir, metadata), source)}
        assert results["XX"].status == "failed"
        assert "Unknown futures root" in results["XX"].error
        assert results["ES"].status == "success"

    def test_empty_file_fails(self, source, catalog_dir, metadata):
        (source / "ES_full_1min_continuous_ratio_adjusted.txt").write_text("")
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "failed"
        assert result.error == "No bars parsed from file"
        assert metadata.rows[(CATALOG, "ES")].bar_count_minute == 0

    def test_invalid_rows_surface_as_warnings(self, source, catalog_dir, metadata):
        path = source / "ES_full_1min_continuous_ratio_adjusted.txt"
        path.write_text("\n".join(_minute_rows(5) + ["2025-06-02 11:00:00,10,9,11,10,1"]) + "\n")
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "success"
        assert result.row_count == 5
        assert result.warnings
