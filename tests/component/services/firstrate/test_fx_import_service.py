"""Component tests for the streaming FX import service (FX M2, task 2.3).

Runs against a real ``ParquetDataCatalog`` in ``tmp_path`` and an in-memory stand-in for
the ``catalog_instruments`` metadata service, like the futures import service tests.
"""

import os
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.fx_import_service import FxImportService, mid_timeframe

pytestmark = pytest.mark.component

CATALOG = "firstrate-fx"
EUR_HOUR = "EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL"
EUR_HOUR_DIR = "EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL"
JPY_DAY = "USD/JPY.IDEALPRO-1-DAY-MID-EXTERNAL"


class FakeMetadataService:
    """In-memory ``get_instrument_sync`` / ``upsert_instrument_sync``."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], CatalogInstrument] = {}

    def get_instrument_sync(self, catalog_name: str, ticker: str):
        return self.rows.get((catalog_name, ticker))

    def upsert_instrument_sync(self, instrument: CatalogInstrument):
        self.rows[(instrument.catalog_name, instrument.ticker)] = instrument
        return instrument


def _hour_rows(count: int) -> list[str]:
    # Monday 2025-07-07 from 00:00 ET; every 3rd price carries stripped zeros.
    rows = []
    for i in range(count):
        price = Decimal("1.17") + Decimal(i) / 100_000
        rows.append(f"20250707,{i:02d}:00:00,{price},{price + Decimal('0.001')},1.16,{price},{i}")
    return rows


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    src = tmp_path / "raw"
    src.mkdir()
    (src / "EURUSD_full_1hour.txt").write_text("\n".join(_hour_rows(20)) + "\n")
    (src / "USDJPY_full_1day.txt").write_text(
        "20140102,108.33599,108.9,107.9,108.5,90000\n"
        "20140103,108.5,109,108.1,108.7,91000\n"
        "20140105,108.7,108.8,108.6,108.7,10\n"  # Sunday-dated daily row
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


def _service(catalog_dir: Path, metadata, chunk_rows: int = 8) -> FxImportService:
    return FxImportService(CatalogManager(catalog_dir), metadata, chunk_rows=chunk_rows)


def _run(service, source: Path, timeframe: str = "1-HOUR-MID"):
    return service.import_directory(source, CATALOG, AssetClass.FX, timeframe)


def _parts(catalog_dir: Path, directory: str = EUR_HOUR_DIR) -> list[Path]:
    return sorted((catalog_dir / CATALOG / "data" / "bar" / directory).glob("*.parquet"))


def _read_all(catalog_dir: Path, bar_type: str = EUR_HOUR) -> list[Bar]:
    return ParquetDataCatalog(str(catalog_dir / CATALOG)).bars(bar_types=[bar_type])


class TestUrisafe:
    @pytest.mark.parametrize(
        ("identifier", "expected"),
        [
            (EUR_HOUR, EUR_HOUR_DIR),
            ("ES.CME-1-MINUTE-LAST-EXTERNAL", "ES.CME-1-MINUTE-LAST-EXTERNAL"),
        ],
    )
    def test_drops_the_slash_like_the_nautilus_catalog(self, identifier, expected):
        # Literal oracle: urisafe_identifier is absent before Nautilus 1.219. That the
        # installed Nautilus writes to this name is pinned by TestFirstImport (real parts).
        from src.services.firstrate.futures_import_service import urisafe

        assert urisafe(identifier) == expected
        assert urisafe(BarType.from_str(identifier)) == expected


class TestMidTimeframe:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [("1-HOUR-LAST", "1-HOUR-MID"), ("1-DAY-MID", "1-DAY-MID"), ("30-MINUTE", "30-MINUTE-MID")],
    )
    def test_price_type_becomes_mid(self, given, expected):
        assert mid_timeframe(given) == expected


class TestFirstImport:
    def test_streams_chunks_into_the_urisafe_bar_directory(self, source, catalog_dir, metadata):
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "success", result.error
        assert (result.ticker, result.outcome, result.row_count) == ("EURUSD", "new", 20)
        assert len(_parts(catalog_dir)) == 3
        assert sum(pq.read_metadata(p).num_rows for p in _parts(catalog_dir)) == 20
        assert not (catalog_dir / CATALOG / "data" / "bar" / "EUR").exists()

    def test_read_back_has_the_pair_precision(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        bars = _read_all(catalog_dir)
        assert len(bars) == 20
        assert {b.open.precision for b in bars} == {5}
        assert str(bars[0].open) == "1.17000"
        assert str(bars[1].open) == "1.17001"

    def test_catalog_row_populated_from_pair_spec(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        row = metadata.rows[(CATALOG, "EURUSD")]
        assert row.ticker == "EURUSD"
        assert row.nautilus_id == "EUR/USD.IDEALPRO"
        assert row.asset_class == "FX"
        assert row.exchange == "IDEALPRO"
        assert row.name == "EUR/USD"
        assert row.currency == "USD"
        assert row.tick_size == Decimal("0.00005")
        assert row.multiplier == Decimal("1")
        assert (row.contract_kind, row.roll_rule, row.expiry) == (None, None, None)
        assert row.bar_count_hourly == 20
        assert row.date_range_end_hourly > row.date_range_start_hourly

    def test_usdjpy_daily_rounding_and_session_warnings(self, source, catalog_dir, metadata):
        [result] = _run(_service(catalog_dir, metadata), source, "1-DAY-MID")
        assert result.status == "success", result.error
        assert result.row_count == 3
        assert "1 row(s) rounded to 3 decimals" in result.warnings
        assert "1 session anomaly bar(s) kept (outside Sun 17:00-Fri 17:00 ET)" in result.warnings
        row = metadata.rows[(CATALOG, "USDJPY")]
        assert (row.currency, row.tick_size) == ("JPY", Decimal("0.005"))
        bars = _read_all(catalog_dir, JPY_DAY)
        assert str(bars[0].open) == "108.336"
        assert bars[0].ts_event == 1388613600 * 1_000_000_000  # 2014-01-01 17:00 EST

    def test_discovers_only_the_requested_timeframe(self, source, catalog_dir, metadata):
        assert _run(_service(catalog_dir, metadata), source, "5-MINUTE-MID") == []
        [daily] = _run(_service(catalog_dir, metadata), source, "1-DAY-MID")
        assert daily.ticker == "USDJPY"


class TestReRun:
    def test_second_run_is_skipped(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.status == "skipped"
        assert len(_parts(catalog_dir)) == 3

    def test_daily_rerun_is_skipped(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source, "1-DAY-MID")
        [again] = _run(_service(catalog_dir, metadata), source, "1-DAY-MID")
        assert again.status == "skipped"

    def test_grown_source_overwrites_without_duplicates(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        (source / "EURUSD_full_1hour.txt").write_text("\n".join(_hour_rows(23)) + "\n")
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 23
        assert metadata.rows[(CATALOG, "EURUSD")].bar_count_hourly == 23

    def test_incomplete_marker_forces_reimport(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        bar_dir = catalog_dir / CATALOG / "data" / "bar" / EUR_HOUR_DIR
        assert not (bar_dir / ".import-incomplete").exists()
        (bar_dir / ".import-incomplete").touch()  # a rewrite that died before it finished
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 20
        assert not (bar_dir / ".import-incomplete").exists()

    def test_partial_parquet_forces_reimport(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        _parts(catalog_dir)[-1].unlink()
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 20


class TestFailures:
    def test_unknown_pair_fails_that_file_only(self, source, catalog_dir, metadata):
        (source / "AUDNZD_full_1hour.txt").write_text("20250707,10:00:00,1.1,1.2,1.0,1.1,1\n")
        results = {r.ticker: r for r in _run(_service(catalog_dir, metadata), source)}
        assert results["AUDNZD"].status == "failed"
        assert "AUDNZD" in results["AUDNZD"].error
        assert results["EURUSD"].status == "success"
        assert (CATALOG, "AUDNZD") not in metadata.rows

    def test_empty_file_fails(self, source, catalog_dir, metadata):
        (source / "EURUSD_full_1hour.txt").write_text("")
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "failed"
        assert result.error == "No bars parsed from file"
        assert metadata.rows[(CATALOG, "EURUSD")].bar_count_hourly == 0

    def test_invalid_rows_surface_as_warnings(self, source, catalog_dir, metadata):
        rows = _hour_rows(5) + ["20250707,06:00:00,1.17,1.16,1.18,1.17,1"]
        (source / "EURUSD_full_1hour.txt").write_text("\n".join(rows) + "\n")
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "success"
        assert result.row_count == 5
        assert result.warnings


#: Damage that reaches a source after a good import: truncated, garbled text, binary junk.
_DAMAGE = {"empty": b"", "garbled": b"not,a,bar\n", "binary": bytes(range(256)) * 8}


class TestDamagedSourceKeepsTheSeries:
    """A source with no readable bar is refused before the existing series is touched."""

    @pytest.mark.parametrize("damage", sorted(_DAMAGE))
    def test_damaged_source_keeps_the_imported_series(self, source, catalog_dir, metadata, damage):
        _run(_service(catalog_dir, metadata), source)
        parts = _parts(catalog_dir)
        (source / "EURUSD_full_1hour.txt").write_bytes(_DAMAGE[damage])
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "failed"
        assert _parts(catalog_dir) == parts  # nothing deleted or appended
        assert len(_read_all(catalog_dir)) == 20
        assert metadata.rows[(CATALOG, "EURUSD")].bar_count_hourly == 20
        bar_dir = catalog_dir / CATALOG / "data" / "bar" / EUR_HOUR_DIR
        assert not (bar_dir / ".import-incomplete").exists()  # the next run still skips

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 file")
    def test_unreadable_source_keeps_the_imported_series(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        path = source / "EURUSD_full_1hour.txt"
        path.chmod(0)
        try:
            [result] = _run(_service(catalog_dir, metadata), source)
        finally:
            path.chmod(0o644)
        assert result.status == "failed"
        assert len(_read_all(catalog_dir)) == 20
        assert metadata.rows[(CATALOG, "EURUSD")].bar_count_hourly == 20
