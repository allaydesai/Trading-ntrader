"""Component tests for the Kraken OHLCVT importer and the ``kraken-crypto`` catalog (crypto M7).

Kraken's downloadable history is headerless ``timestamp,open,high,low,close,volume,trades``
CSVs, one per pair and interval: ``XBTUSD_60.csv`` (UNIX seconds, bar open, UTC). The importer
reuses the FirstRate crypto service (chunked write, footer verification, re-run classifier),
so these tests pin only what differs: discovery by Kraken altname and interval, epoch stamps,
the trades column, and the UTC source-end probe. Real ``ParquetDataCatalog`` in ``tmp_path``.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.models.catalog import AssetClass
from src.services.crypto.kraken_import import (
    KrakenImportService,
    discover_kraken_files,
    kraken_source_last_date,
)
from src.services.firstrate.catalog_manager import CatalogManager
from tests.component.services.firstrate.test_crypto_import_service import FakeMetadataService

pytestmark = pytest.mark.component

CATALOG = "kraken-crypto"
H0 = 1710028800  # 2024-03-10 00:00 UTC (a US DST Sunday: every UTC hour once)


def _hour_rows(count: int) -> str:
    rows = [
        f"{H0 + 3600 * i},68000.{i},{68005 + i},67995,68000.{i},{i}.10000000000002,{7 + i}"
        for i in range(count)
    ]
    return "\n".join(rows) + "\n"


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    src = tmp_path / "raw"
    src.mkdir()
    (src / "XBTUSD_60.csv").write_text(_hour_rows(20))
    (src / "XBTUSD_15.csv").write_text(_hour_rows(2))  # no catalog field for 15-minute
    (src / "DOGEUSD_60.csv").write_text(_hour_rows(2))  # no coin spec: not ours, ignored
    (src / "XBTUSDT_60.csv").write_text(_hour_rows(2))  # another pair (USDT quote)
    (src / "SOLUSD_1440.csv").write_text(
        "1710028800,145.123456,150,140,148,1000.5,99\n"  # 6 decimals: rounded to 4, counted
        "1710115200,148,151,147,150,0,0\n"
    )
    return src


@pytest.fixture()
def catalog_dir(tmp_path: Path) -> Path:
    base = tmp_path / "catalogs"
    (base / CATALOG).mkdir(parents=True)
    return base


def _service(catalog_dir: Path, metadata, chunk_rows: int = 8) -> KrakenImportService:
    return KrakenImportService(CatalogManager(catalog_dir), metadata, chunk_rows=chunk_rows)


def _run(service, source: Path, timeframe: str):
    return service.import_directory(source, CATALOG, AssetClass.CRYPTO, timeframe)


class TestDiscovery:
    def test_files_map_to_spec_coins_by_altname_and_interval(self, source):
        found = {(coin, tf, p.name) for coin, tf, p in discover_kraken_files(source)}
        assert found == {
            ("BTC", "1-HOUR-LAST", "XBTUSD_60.csv"),
            ("SOL", "1-DAY-LAST", "SOLUSD_1440.csv"),
        }

    def test_one_directory_down_is_found_too(self, tmp_path: Path):
        (tmp_path / "Kraken_OHLCVT").mkdir()
        (tmp_path / "Kraken_OHLCVT" / "ETHUSD_5.csv").write_text("1710028800,1,1,1,1,1,1\n")
        assert [(c, tf) for c, tf, _ in discover_kraken_files(tmp_path)] == [
            ("ETH", "5-MINUTE-LAST")
        ]


def test_source_last_date_reads_epoch_seconds_as_utc(source):
    assert kraken_source_last_date(source / "XBTUSD_60.csv") == datetime(
        2024, 3, 10, 19, tzinfo=timezone.utc
    )
    assert kraken_source_last_date(source / "missing.csv") is None


class TestImport:
    def test_hourly_btc_lands_under_the_spot_id_with_epoch_stamps(self, source, catalog_dir):
        metadata = FakeMetadataService()
        results = _run(_service(catalog_dir, metadata), source, "1-HOUR-LAST")
        assert [(r.ticker, r.status, r.row_count) for r in results] == [("BTC", "success", 20)]
        bars = ParquetDataCatalog(str(catalog_dir / CATALOG)).bars(
            bar_types=["BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL"]
        )
        assert len(bars) == 20
        assert bars[0].ts_event == H0 * 10**9  # bar open, UTC
        assert str(bars[0].open) == "68000.0000"  # spec precision 4
        assert str(bars[3].volume) == "3.10000000"  # float noise cut at 8 decimals
        row = metadata.rows[(CATALOG, "BTC")]
        assert row.nautilus_id == "BTC/USD.KRAKEN" and row.bar_count_hourly == 20
        assert row.date_range_end_hourly == datetime(2024, 3, 10, 19, tzinfo=timezone.utc)

    def test_daily_sol_rounds_and_reports_it(self, source, catalog_dir):
        results = _run(_service(catalog_dir, FakeMetadataService()), source, "1-DAY-LAST")
        (sol,) = results
        assert sol.status == "success" and sol.row_count == 2
        assert "1 row(s) rounded to 4 decimals" in sol.warnings
        assert "1 zero-volume bar(s) kept" in sol.warnings
        assert not any("FirstRate" in w for w in sol.warnings)

    def test_a_rerun_of_an_unchanged_source_is_skipped(self, source, catalog_dir):
        metadata = FakeMetadataService()
        _run(_service(catalog_dir, metadata), source, "1-HOUR-LAST")
        again = _run(_service(catalog_dir, metadata), source, "1-HOUR-LAST")
        assert [(r.ticker, r.status) for r in again] == [("BTC", "skipped")]

    def test_an_extended_source_is_reimported(self, source, catalog_dir):
        metadata = FakeMetadataService()
        _run(_service(catalog_dir, metadata), source, "1-HOUR-LAST")
        (source / "XBTUSD_60.csv").write_text(_hour_rows(22))
        again = _run(_service(catalog_dir, metadata), source, "1-HOUR-LAST")
        assert [(r.status, r.outcome, r.row_count) for r in again] == [
            ("success", "reimported", 22)
        ]
