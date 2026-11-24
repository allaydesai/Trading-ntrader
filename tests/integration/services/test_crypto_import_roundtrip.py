"""Crypto M2 (task 2.7): import trimmed real FirstRate crypto files end to end, then feed a
BacktestEngine.

Fixtures under ``tests/fixtures/firstrate_crypto/`` are excerpts of the FirstRate files:
BTC 1-hour on 2016-08-03 (4-decimal prices), 2022-11-10 (a 3-hour gap), the 2024-03-08..11
weekend with the US spring-forward Sunday (24 UTC hours, no shift), 2024-12-02 (the longest
hourly gap, 8 hours) and 2026-03-20..21 (volume becomes whole coins); SOL daily for March
2021, 2025-01-10..02-05 (4-decimal prices) and 2026-03-10..05-02. Every bar read back must
equal the source row at its UTC bar open, prices rounded half-even to 4 decimals and volume
exact at 8. Run with ``--forked`` (C extensions).
"""

from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass
from src.services.firstrate.backtest_loader import build_instrument
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.crypto_import_service import CryptoImportService

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "firstrate_crypto"
CATALOG = "firstrate-crypto"
GRID = Decimal("0.0001")
CASES = {
    "btc-hour": ("BTC", "BTC/USD.KRAKEN", "1-HOUR-LAST", "BTC_full_1hour.txt"),
    "sol-day": ("SOL", "SOL/USD.KRAKEN", "1-DAY-LAST", "SOL_full_1day.txt"),
}


class _Metadata:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], CatalogInstrument] = {}

    def get_instrument_sync(self, catalog_name, ticker):
        return self.rows.get((catalog_name, ticker))

    def upsert_instrument_sync(self, instrument):
        self.rows[(instrument.catalog_name, instrument.ticker)] = instrument
        return instrument


def _source_rows(name: str) -> list[list[str]]:
    return [line.split(",") for line in (FIXTURES / name).read_text().splitlines() if line]


def _utc_ns(stamp: str) -> int:
    """The bar open in UTC ns: the stamp as written (UTC), a daily row at 00:00."""
    fmt = "%Y-%m-%d %H:%M:%S" if " " in stamp else "%Y-%m-%d"
    return int(datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc).timestamp()) * 10**9


def _run_engine(instrument, bars) -> None:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=Venue("KRAKEN"),
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            default_leverage=Decimal(1),
            starting_balances=[Money(100_000, USD)],
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.run()
    finally:
        engine.dispose()


def _import(tmp_path: Path, timeframe: str):
    (tmp_path / "catalogs" / CATALOG).mkdir(parents=True, exist_ok=True)
    metadata = _Metadata()
    service = CryptoImportService(CatalogManager(tmp_path / "catalogs"), metadata, chunk_rows=25)
    results = service.import_directory(FIXTURES, CATALOG, AssetClass.CRYPTO, timeframe)
    return service, metadata, {r.ticker: r for r in results}


@pytest.mark.parametrize("case", list(CASES))
def test_import_round_trip_and_engine_accepts_bars(tmp_path, case):
    coin, nautilus_id, timeframe, filename = CASES[case]
    service, metadata, results = _import(tmp_path, timeframe)
    result = results[coin]

    source = _source_rows(filename)
    assert result.status == "success", result.error
    assert result.row_count == len(source)

    catalog = ParquetDataCatalog(str(tmp_path / "catalogs" / CATALOG))
    bars = catalog.bars(bar_types=[f"{nautilus_id}-{timeframe}-EXTERNAL"])
    assert len(bars) == len(source)
    for bar, row in zip(bars, source):
        assert bar.ts_init == bar.ts_event == _utc_ns(row[0])
        for price, raw in zip((bar.open, bar.high, bar.low, bar.close), row[1:5]):
            assert price.precision == 4
            assert price.as_decimal() == Decimal(raw).quantize(GRID, rounding=ROUND_HALF_EVEN)
        assert bar.volume.precision == 8
        assert bar.volume.as_decimal() == Decimal(row[5])

    row = metadata.rows[(CATALOG, coin)]
    assert (row.asset_class, row.nautilus_id, row.exchange) == ("CRYPTO", nautilus_id, "KRAKEN")

    instrument = build_instrument(row.asset_class, row.nautilus_id, coin, bars)
    assert (instrument.price_precision, instrument.size_precision) == (4, 8)
    _run_engine(instrument, bars)

    again = service.import_directory(FIXTURES, CATALOG, AssetClass.CRYPTO, timeframe)
    assert {r.ticker: r.status for r in again}[coin] == "skipped"


def test_dst_sunday_keeps_24_utc_hours_and_anomalies_are_reported(tmp_path):
    _, _, results = _import(tmp_path, "1-HOUR-LAST")
    catalog = ParquetDataCatalog(str(tmp_path / "catalogs" / CATALOG))
    bars = catalog.bars(bar_types=["BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL"])
    sunday = [b for b in bars if _utc_ns("2024-03-10") <= b.ts_event < _utc_ns("2024-03-11")]
    assert [b.ts_event for b in sunday] == [_utc_ns(f"2024-03-10 {h:02d}:00:00") for h in range(24)]
    warnings = results["BTC"].warnings
    assert "volume is whole-coin from 2026-03-21 00:00 UTC (FirstRate rounding)" in warnings
    # the four jumps between excerpt windows; the real 3- and 8-hour gaps stay under 10 bars
    assert any(w.startswith("4 gap(s) longer than 10 bars") for w in warnings)
