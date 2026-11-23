"""FX M2 (task 2.7): import trimmed real FirstRate FX files end to end, then feed a
BacktestEngine.

Fixtures under ``tests/fixtures/firstrate_fx/`` are excerpts of the FirstRate FX files:
EURUSD 1-hour over the 2023-08-05 weekend (the Saturday bar) and the 2025-03-09 DST week;
USDJPY 1-hour on 2014-10-24..27, where the source carries float noise (``108.33599``); and
USDJPY daily for October 2014 (5 padded decimals). Every bar read back must equal the source
row rounded half-even to the pair precision, at the New York bar open. Run with ``--forked``
(C extensions).
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

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
from src.services.firstrate.fx_import_service import FxImportService

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "firstrate_fx"
CATALOG = "firstrate-fx"
NEW_YORK = ZoneInfo("America/New_York")
CASES = {
    "eurusd-hour": ("EURUSD", "EUR/USD.IDEALPRO", "1-HOUR-MID", 5, "EURUSD_full_1hour.txt"),
    "usdjpy-hour": ("USDJPY", "USD/JPY.IDEALPRO", "1-HOUR-MID", 3, "USDJPY_full_1hour.txt"),
    "usdjpy-day": ("USDJPY", "USD/JPY.IDEALPRO", "1-DAY-MID", 3, "USDJPY_full_1day.txt"),
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


def _expected_ns(row: list[str]) -> int:
    """Bar open in UTC ns: intraday at the wall clock, daily at ``D-1 17:00`` New York."""
    day = datetime.strptime(row[0], "%Y%m%d")
    if len(row) == 7:
        local = datetime.strptime(f"{row[0]} {row[1]}", "%Y%m%d %H:%M:%S")
    else:
        local = day - timedelta(days=1) + timedelta(hours=17)
    return int(local.replace(tzinfo=NEW_YORK).timestamp()) * 1_000_000_000


def _run_engine(instrument, bars) -> None:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=Venue("IDEALPRO"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(1_000_000, USD)],
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.run()
    finally:
        engine.dispose()


@pytest.mark.parametrize("case", list(CASES))
def test_import_round_trip_and_engine_accepts_bars(tmp_path, case):
    pair, nautilus_id, timeframe, precision, filename = CASES[case]
    (tmp_path / "catalogs" / CATALOG).mkdir(parents=True)
    metadata = _Metadata()
    service = FxImportService(CatalogManager(tmp_path / "catalogs"), metadata, chunk_rows=25)

    results = service.import_directory(FIXTURES, CATALOG, AssetClass.FX, timeframe)
    [result] = [r for r in results if r.ticker == pair]

    source = _source_rows(filename)
    assert result.status == "success", result.error
    assert result.row_count == len(source)

    catalog = ParquetDataCatalog(str(tmp_path / "catalogs" / CATALOG))
    bars = catalog.bars(bar_types=[f"{nautilus_id}-{timeframe}-EXTERNAL"])
    assert len(bars) == len(source)
    grid = Decimal(1).scaleb(-precision)
    for bar, row in zip(bars, source):
        assert bar.ts_init == bar.ts_event == _expected_ns(row)
        prices = row[-5:-1]
        for price, raw in zip((bar.open, bar.high, bar.low, bar.close), prices):
            assert price.precision == precision
            assert price.as_decimal() == Decimal(raw).quantize(grid, rounding=ROUND_HALF_EVEN)
        assert bar.volume.as_decimal() == Decimal(row[-1])

    row = metadata.rows[(CATALOG, pair)]
    assert (row.asset_class, row.nautilus_id, row.exchange) == ("FX", nautilus_id, "IDEALPRO")

    instrument = build_instrument(row.asset_class, row.nautilus_id, pair, bars)
    assert instrument.price_precision == precision
    _run_engine(instrument, bars)

    again = service.import_directory(FIXTURES, CATALOG, AssetClass.FX, timeframe)
    assert {r.ticker: r.status for r in again}[pair] == "skipped"


def test_real_anomalies_and_noise_are_reported(tmp_path):
    (tmp_path / "catalogs" / CATALOG).mkdir(parents=True)
    service = FxImportService(CatalogManager(tmp_path / "catalogs"), _Metadata())
    results = {
        r.ticker: r for r in service.import_directory(FIXTURES, CATALOG, AssetClass.FX, "1-HOUR")
    }
    assert any("session anomaly" in w for w in results["EURUSD"].warnings)
    assert "1 session anomaly bar(s) kept" in " ".join(results["EURUSD"].warnings)
    assert any("rounded to 3 decimals" in w for w in results["USDJPY"].warnings)
