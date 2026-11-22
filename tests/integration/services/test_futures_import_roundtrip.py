"""Futures M2: import trimmed real FirstRate files end to end, then feed a BacktestEngine.

Fixtures under ``tests/fixtures/firstrate_futures/`` are excerpts of the FirstRate
continuous ratio-adjusted files: ES 1-hour across the 2025-03-09 DST change (rows carry
1 and 2 decimals) and the last 30 ZN daily rows (7 columns, 6 decimals). Run with
``--forked`` (C extensions).
"""

from decimal import Decimal
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
from src.services.firstrate.futures_import_service import FuturesImportService
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "firstrate_futures"
CATALOG = "firstrate-futures"
CASES = {
    "1-HOUR-LAST": ("ES", "ES.CME", "CME", 2, "ES_full_1hour_continuous_ratio_adjusted.txt"),
    "1-DAY-LAST": ("ZN", "ZN.CBOT", "CBOT", 6, "ZN_full_1day_continuous_ratio_adjusted.txt"),
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


def _run_engine(instrument, bars, venue: str) -> None:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=Venue(venue),
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


@pytest.mark.parametrize("timeframe", list(CASES))
def test_import_round_trip_and_engine_accepts_bars(tmp_path, timeframe):
    root, nautilus_id, venue, precision, filename = CASES[timeframe]
    (tmp_path / "catalogs" / CATALOG).mkdir(parents=True)
    metadata = _Metadata()
    service = FuturesImportService(CatalogManager(tmp_path / "catalogs"), metadata, chunk_rows=25)

    [result] = service.import_directory(FIXTURES, CATALOG, AssetClass.FUTURES, timeframe)

    source = _source_rows(filename)
    assert result.status == "success", result.error
    assert result.row_count == len(source)

    bar_type = f"{nautilus_id}-{timeframe}-EXTERNAL"
    catalog = ParquetDataCatalog(str(tmp_path / "catalogs" / CATALOG))
    bars = catalog.bars(bar_types=[bar_type])
    assert len(bars) == len(source)
    for bar, row in zip(bars, source):
        expected_ts = FirstRateCsvParser._parse_timestamp(row[0])
        assert bar.ts_init == int(expected_ts.timestamp()) * 1_000_000_000
        for price, raw in zip((bar.open, bar.high, bar.low, bar.close), row[1:5]):
            assert price.precision == precision
            assert price.as_decimal() == Decimal(raw)
        assert bar.volume.as_decimal() == Decimal(row[5])

    row = metadata.rows[(CATALOG, root)]
    assert (row.asset_class, row.nautilus_id, row.contract_kind) == (
        "FUTURES",
        nautilus_id,
        "continuous",
    )

    instrument = build_instrument(row.asset_class, row.nautilus_id, root, bars)
    assert instrument.price_precision == precision
    _run_engine(instrument, bars, venue)

    [again] = service.import_directory(FIXTURES, CATALOG, AssetClass.FUTURES, timeframe)
    assert again.status == "skipped"
