"""Component tests for the streaming crypto import service (crypto M2, task 2.3).

Runs against a real ``ParquetDataCatalog`` in ``tmp_path`` and an in-memory stand-in for the
``catalog_instruments`` metadata service, like the FX and futures import service tests.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.crypto_import_service import CryptoImportService, last_timeframe

pytestmark = pytest.mark.component

CATALOG = "firstrate-crypto"
BTC_HOUR = "BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL"
BTC_HOUR_DIR = "BTCUSD.KRAKEN-1-HOUR-LAST-EXTERNAL"
SOL_DAY = "SOL/USD.KRAKEN-1-DAY-LAST-EXTERNAL"


class FakeMetadataService:
    """In-memory ``get_instrument_sync`` / ``upsert_instrument_sync``."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], CatalogInstrument] = {}

    def get_instrument_sync(self, catalog_name: str, ticker: str):
        return self.rows.get((catalog_name, ticker))

    def upsert_instrument_sync(self, instrument: CatalogInstrument):
        self.rows[(instrument.catalog_name, instrument.ticker)] = instrument
        return instrument


def _hour_rows(count: int, day: str = "2024-03-10") -> list[str]:
    # A US DST Sunday: UTC data has every hour exactly once.
    rows = []
    for i in range(count):
        price = Decimal("68000.1") + Decimal(i)
        rows.append(f"{day} {i:02d}:00:00,{price},{price + 5},{price - 5},{price},{i}.125")
    return rows


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    src = tmp_path / "raw"
    src.mkdir()
    (src / "BTC_full_1hour.txt").write_text("\n".join(_hour_rows(20)) + "\n")
    (src / "SOL_full_1day.txt").write_text(
        "2026-03-19,90.12345,92,89,91,150000.5\n"  # 5 decimals: rounded and counted
        "2026-03-20,91,93,90,92,140000.25\n"
        "2026-03-21,92,94,91,93,130000\n"  # whole-coin volume from here
        "2026-05-01,93,95,92,94,0\n"  # 40 missing days and a zero volume
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


def _service(catalog_dir: Path, metadata, chunk_rows: int = 8) -> CryptoImportService:
    return CryptoImportService(CatalogManager(catalog_dir), metadata, chunk_rows=chunk_rows)


def _run(service, source: Path, timeframe: str = "1-HOUR-LAST"):
    return service.import_directory(source, CATALOG, AssetClass.CRYPTO, timeframe)


def _parts(catalog_dir: Path, directory: str = BTC_HOUR_DIR) -> list[Path]:
    return sorted((catalog_dir / CATALOG / "data" / "bar" / directory).glob("*.parquet"))


def _read_all(catalog_dir: Path, bar_type: str = BTC_HOUR) -> list[Bar]:
    return ParquetDataCatalog(str(catalog_dir / CATALOG)).bars(bar_types=[bar_type])


def _ns(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()) * 10**9


class TestLastTimeframe:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("1-HOUR-MID", "1-HOUR-LAST"),
            ("1-DAY-LAST", "1-DAY-LAST"),
            ("5-MINUTE", "5-MINUTE-LAST"),
        ],
    )
    def test_price_type_is_last(self, given, expected):
        assert last_timeframe(given) == expected


class TestFirstImport:
    def test_streams_chunks_into_the_urisafe_bar_directory(self, source, catalog_dir, metadata):
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "success", result.error
        assert (result.ticker, result.outcome, result.row_count) == ("BTC", "new", 20)
        assert len(_parts(catalog_dir)) == 3
        assert sum(pq.read_metadata(p).num_rows for p in _parts(catalog_dir)) == 20

    def test_read_back_precision_and_utc_stamps(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        bars = _read_all(catalog_dir)
        assert len(bars) == 20
        assert {b.open.precision for b in bars} == {4}
        assert {b.volume.precision for b in bars} == {8}
        assert (str(bars[0].open), str(bars[3].volume)) == ("68000.1000", "3.12500000")
        assert [b.ts_event for b in bars] == [_ns(f"2024-03-10T{h:02d}:00:00") for h in range(20)]

    def test_catalog_row_populated_from_coin_spec(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        row = metadata.rows[(CATALOG, "BTC")]
        assert row.ticker == "BTC"
        assert row.nautilus_id == "BTC/USD.KRAKEN"
        assert row.asset_class == "CRYPTO"
        assert row.exchange == "KRAKEN"
        assert row.name == "BTC/USD"
        assert row.currency == "USD"
        assert row.tick_size == Decimal("0.0001")
        assert row.multiplier == Decimal("1")
        assert (row.contract_kind, row.roll_rule, row.expiry) == (None, None, None)
        assert row.bar_count_hourly == 20
        assert row.date_range_start_hourly == datetime(2024, 3, 10, tzinfo=timezone.utc)
        assert row.date_range_end_hourly == datetime(2024, 3, 10, 19, tzinfo=timezone.utc)

    def test_daily_warnings_and_midnight_stamps(self, source, catalog_dir, metadata):
        [result] = _run(_service(catalog_dir, metadata), source, "1-DAY-LAST")
        assert result.status == "success", result.error
        assert result.row_count == 4
        assert result.warnings == [
            "1 row(s) rounded to 4 decimals",
            "1 zero-volume bar(s) kept",
            "1 gap(s) longer than 10 bars (longest 40 bars missing)",
            "volume is whole-coin from 2026-03-21 00:00 UTC (FirstRate rounding)",
        ]
        assert result.ohlc_flags == 0
        bars = _read_all(catalog_dir, SOL_DAY)
        assert str(bars[0].open) == "90.1234"
        assert bars[0].ts_event == _ns("2026-03-19T00:00:00")
        assert metadata.rows[(CATALOG, "SOL")].nautilus_id == "SOL/USD.KRAKEN"

    def test_discovers_only_the_requested_timeframe(self, source, catalog_dir, metadata):
        assert _run(_service(catalog_dir, metadata), source, "5-MINUTE-LAST") == []
        [daily] = _run(_service(catalog_dir, metadata), source, "1-DAY-LAST")
        assert daily.ticker == "SOL"

    def test_discovers_files_one_directory_down(self, tmp_path, catalog_dir, metadata):
        nested = tmp_path / "nested" / "crypto"
        nested.mkdir(parents=True)
        (nested / "BTC_full_1hour.txt").write_text("\n".join(_hour_rows(3)) + "\n")
        [result] = _run(_service(catalog_dir, metadata), tmp_path / "nested")
        assert (result.ticker, result.status) == ("BTC", "success")


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

    def test_late_evening_utc_end_is_still_skipped(self, tmp_path, catalog_dir, metadata):
        # 23:00 UTC is the next day in no timezone shift; an ET reading would move it.
        src = tmp_path / "late"
        src.mkdir()
        rows = [f"2026-05-02 {h:02d}:00:00,1,1,1,1,1" for h in range(20, 24)]
        (src / "BTC_full_1hour.txt").write_text("\n".join(rows) + "\n")
        _run(_service(catalog_dir, metadata), src)
        [again] = _run(_service(catalog_dir, metadata), src)
        assert again.status == "skipped"

    def test_grown_source_overwrites_without_duplicates(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        (source / "BTC_full_1hour.txt").write_text("\n".join(_hour_rows(23)) + "\n")
        [again] = _run(_service(catalog_dir, metadata), source)
        assert again.outcome == "reimported"
        assert len(_read_all(catalog_dir)) == 23
        assert metadata.rows[(CATALOG, "BTC")].bar_count_hourly == 23

    def test_incomplete_marker_forces_reimport(self, source, catalog_dir, metadata):
        _run(_service(catalog_dir, metadata), source)
        bar_dir = catalog_dir / CATALOG / "data" / "bar" / BTC_HOUR_DIR
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
    def test_unknown_coin_fails_that_file_only(self, source, catalog_dir, metadata):
        (source / "DOGE_full_1hour.txt").write_text("2024-01-01 00:00:00,1,1,1,1,1\n")
        results = {r.ticker: r for r in _run(_service(catalog_dir, metadata), source)}
        assert results["DOGE"].status == "failed"
        assert "DOGE" in results["DOGE"].error
        assert results["BTC"].status == "success"
        assert (CATALOG, "DOGE") not in metadata.rows

    def test_empty_file_fails(self, source, catalog_dir, metadata):
        (source / "BTC_full_1hour.txt").write_text("")
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "failed"
        assert result.error == "No bars parsed from file"
        assert metadata.rows[(CATALOG, "BTC")].bar_count_hourly == 0

    @pytest.mark.parametrize("damage", ["", "not,a,bar\n"])
    def test_unreadable_source_keeps_the_imported_series(
        self, source, catalog_dir, metadata, damage
    ):
        _run(_service(catalog_dir, metadata), source)
        (source / "BTC_full_1hour.txt").write_text(damage)  # truncated or garbled later
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "failed"
        assert "no readable bars" in result.error
        assert len(_read_all(catalog_dir)) == 20  # nothing deleted
        assert metadata.rows[(CATALOG, "BTC")].bar_count_hourly == 20

    def test_invalid_rows_surface_as_ohlc_warnings(self, source, catalog_dir, metadata):
        rows = _hour_rows(5) + ["2024-03-10 06:00:00,100,99,101,100,1"]
        (source / "BTC_full_1hour.txt").write_text("\n".join(rows) + "\n")
        [result] = _run(_service(catalog_dir, metadata), source)
        assert result.status == "success"
        assert result.row_count == 5
        assert result.ohlc_flags >= 1 and result.warnings
