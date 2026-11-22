"""Regression: equity/ETF files with mixed decimal counts keep every digit (futures M2).

Nautilus' Parquet catalog stores a batch at its FIRST bar's price precision. A FirstRate
file whose first row is ``5918.9`` and a later row ``5918.99`` used to come back from the
catalog as ``5918.9``. The equity parser now emits one fixed precision per file (the most
decimals any price in it carries).
"""

from pathlib import Path

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser

pytestmark = pytest.mark.component

BAR_TYPE = BarType.from_str("SPY.ARCA-1-MINUTE-LAST-EXTERNAL")


def _parse(tmp_path: Path, lines: list[str]):
    path = tmp_path / "SPY_full_1min_adjsplitdiv.txt"
    path.write_text("\n".join(lines) + "\n")
    return FirstRateCsvParser().parse_file(path, BAR_TYPE.instrument_id, BAR_TYPE)


MIXED = [
    "2020-09-09 09:30:00,5918.9,5919,5918.5,5918.9,100",
    "2020-09-09 09:31:00,5918.99,5919.25,5918.75,5919.01,200",
    "2020-09-09 09:32:00,5919.1,5919.2,5919,5919.1,300",
]


def test_mixed_decimals_share_the_widest_precision(tmp_path):
    bars = _parse(tmp_path, MIXED)
    assert {p.precision for b in bars for p in (b.open, b.high, b.low, b.close)} == {2}
    assert [str(b.open) for b in bars] == ["5918.90", "5918.99", "5919.10"]


def test_catalog_round_trip_keeps_every_digit(tmp_path):
    bars = _parse(tmp_path, MIXED)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    catalog.write_data(bars)
    back = catalog.bars(bar_types=[str(BAR_TYPE)])
    assert [str(b.open) for b in back] == ["5918.90", "5918.99", "5919.10"]
    assert [str(b.close) for b in back] == ["5918.90", "5919.01", "5919.10"]


def test_uniform_file_is_unchanged(tmp_path):
    bars = _parse(
        tmp_path,
        [
            "2020-09-09 09:30:00,25.1000,25.2000,25.0500,25.1500,100",
            "2020-09-09 09:31:00,25.2000,25.3000,25.1000,25.2500,200",
        ],
    )
    assert [str(b.open) for b in bars] == ["25.1000", "25.2000"]
    assert bars[0].open.precision == 4


def test_integer_prices_stay_precision_zero(tmp_path):
    bars = _parse(tmp_path, ["2020-09-09 09:30:00,25,26,24,25,100"])
    assert str(bars[0].open) == "25"
