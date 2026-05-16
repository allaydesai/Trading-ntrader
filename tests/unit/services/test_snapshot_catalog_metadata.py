"""Unit tests for ``scripts/diagnostics/snapshot_catalog_metadata``.

The snapshot tool is read-only — it queries ``catalog_instruments`` and writes
JSON. These tests cover the pure-Python pieces (serialization, write-snapshot)
without touching the database.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def diagnostics_module():
    """Import the snapshot module dynamically (not a package — script path).

    The script lives outside ``src/`` so we load it by file path to keep the
    tests resilient to where the project root is configured.
    """
    import importlib.util

    script_path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "diagnostics"
        / "snapshot_catalog_metadata.py"
    )
    spec = importlib.util.spec_from_file_location("snapshot_catalog_metadata", script_path)
    assert spec is not None and spec.loader is not None, f"cannot load {script_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_row(
    *,
    row_id: int,
    ticker: str,
    catalog_name: str = "e2e-test",
    nautilus_id: str = "AAPL.NASDAQ",
    asset_class: str = "STOCK",
    daily: int = 6601,
    hourly: int = 89378,
    five_min: int = 864403,
    minute: int = 3439333,
    start: datetime | None = None,
    end: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = row_id
    row.ticker = ticker
    row.catalog_name = catalog_name
    row.nautilus_id = nautilus_id
    row.asset_class = asset_class
    row.bar_count_daily = daily
    row.bar_count_hourly = hourly
    row.bar_count_5min = five_min
    row.bar_count_minute = minute
    row.date_range_start = start
    row.date_range_end = end
    return row


def test_serialize_instrument_flattens_per_timeframe_counts(diagnostics_module):
    row = _build_row(
        row_id=1,
        ticker="AAPL",
        start=datetime(2018, 1, 2, 14, 30, tzinfo=timezone.utc),
        end=datetime(2018, 12, 31, 21, 0, tzinfo=timezone.utc),
    )
    out = diagnostics_module._serialize_instrument(row)

    assert out["ticker"] == "AAPL"
    assert out["nautilus_id"] == "AAPL.NASDAQ"
    assert out["asset_class"] == "STOCK"
    assert out["catalog_instrument_id"] == 1
    assert out["date_range_start"] == "2018-01-02T14:30:00+00:00"
    assert out["date_range_end"] == "2018-12-31T21:00:00+00:00"
    assert out["timeframes"] == {
        "1-DAY": {"bar_count": 6601},
        "1-HOUR": {"bar_count": 89378},
        "5-MINUTE": {"bar_count": 864403},
        "1-MINUTE": {"bar_count": 3439333},
    }


def test_serialize_instrument_handles_null_date_ranges(diagnostics_module):
    row = _build_row(row_id=2, ticker="NEW", start=None, end=None)
    out = diagnostics_module._serialize_instrument(row)
    assert out["date_range_start"] is None
    assert out["date_range_end"] is None


def test_write_snapshot_writes_pretty_json_and_creates_parents(diagnostics_module, tmp_path):
    payload = {
        "captured_at": "2026-05-10T00:00:00+00:00",
        "catalogs": [
            {"catalog_name": "e2e-test", "instruments": []},
        ],
    }
    out_path = tmp_path / "nested" / "dir" / "snapshot.json"

    diagnostics_module.write_snapshot(payload, out_path)

    assert out_path.exists()
    written = json.loads(out_path.read_text())
    assert written == payload
    # sort_keys=True is what makes diffs stable across runs.
    assert out_path.read_text().splitlines()[1].lstrip().startswith('"captured_at"')
