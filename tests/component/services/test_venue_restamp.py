"""Component tests for the venue re-stamp executor, against real parquet files.

Fixtures are written with the real ``ParquetDataCatalog``, not hand-rolled: the
whole point of the rewrite is that Nautilus still reads the result, so a
hand-built file that merely resembles one would test nothing. These cover the
mechanics and the verification; the end-to-end proof that a restamped partition
loads through the production path lives in the integration tier.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.services.firstrate.venue_restamp import (
    ActionState,
    classify_action_state,
    execute_action,
    restamp_file,
    verify_file,
    verify_row_groups,
)
from src.services.firstrate.venue_restamp_plan import build_restamp_plan

pytestmark = pytest.mark.component

_START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _write_bars(
    catalog_root: Path, instrument_id: str, spec: str, count: int, *, price_offset: int = 0
) -> Path:
    """Write a real catalog partition and return its directory.

    ``price_offset`` shifts the price series so two partitions can be made to
    differ in *values* while keeping their row counts and layout identical — the
    only way to prove the statistics comparison catches content drift rather than
    just size drift.
    """
    bar_type = BarType.from_str(f"{instrument_id}-{spec}-EXTERNAL")
    bars = []
    for i in range(count):
        ts = int((_START + timedelta(days=i)).timestamp() * 1e9)
        price = 100 + i + price_offset
        bars.append(
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
    ParquetDataCatalog(str(catalog_root)).write_data(bars)
    return catalog_root / "data" / "bar" / f"{instrument_id}-{spec}-EXTERNAL"


def _only_file(directory: Path) -> Path:
    files = sorted(directory.glob("*.parquet"))
    assert len(files) == 1, f"expected one parquet in {directory}, found {len(files)}"
    return files[0]


def _plan_one(catalog_root: Path, ticker: str, new_venue: str):
    plan = build_restamp_plan(
        catalog="test", catalog_root=catalog_root, target_venues={ticker: new_venue}
    )
    assert plan.is_safe, f"plan not safe: {plan}"
    assert len(plan.actions) == 1
    return plan.actions[0]


@pytest.fixture
def catalog_root(tmp_path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    return root


class TestRestampFile:
    """The rewrite itself: identity changes, everything else does not."""

    def test_both_metadata_keys_are_restamped(self, catalog_root):
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 20)
        src = _only_file(src_dir)
        dst = catalog_root / "out.parquet"

        result = restamp_file(
            src,
            dst,
            new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL",
            new_instrument_id="AAA.ARCA",
        )

        metadata = pq.ParquetFile(dst).schema_arrow.metadata
        assert result.ok
        assert metadata[b"bar_type"] == b"AAA.ARCA-1-DAY-LAST-EXTERNAL"
        assert metadata[b"instrument_id"] == b"AAA.ARCA"

    def test_precision_metadata_is_preserved(self, catalog_root):
        """Precision is a per-instrument fact; a venue fix must not touch it."""
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 20)
        src = _only_file(src_dir)
        dst = catalog_root / "out.parquet"
        before = dict(pq.ParquetFile(src).schema_arrow.metadata)

        restamp_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )

        after = dict(pq.ParquetFile(dst).schema_arrow.metadata)
        assert after[b"price_precision"] == before[b"price_precision"]
        assert after[b"size_precision"] == before[b"size_precision"]

    def test_row_count_and_column_schema_are_unchanged(self, catalog_root):
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 137)
        src = _only_file(src_dir)
        dst = catalog_root / "out.parquet"

        result = restamp_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )

        src_file, dst_file = pq.ParquetFile(src), pq.ParquetFile(dst)
        assert result.rows_in == result.rows_out == 137
        assert dst_file.schema_arrow.remove_metadata().equals(
            src_file.schema_arrow.remove_metadata()
        )

    def test_ohlcv_values_are_copied_verbatim(self, catalog_root):
        """The reason to re-stamp rather than re-import: bars are never re-derived."""
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 50)
        src = _only_file(src_dir)
        dst = catalog_root / "out.parquet"

        restamp_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )

        src_table = pq.read_table(src).replace_schema_metadata(None)
        dst_table = pq.read_table(dst).replace_schema_metadata(None)
        assert src_table.equals(dst_table)

    def test_output_keeps_the_catalog_encoding(self, catalog_root):
        """Compression and format must match, or Nautilus reads a different file."""
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 20)
        src = _only_file(src_dir)
        dst = catalog_root / "out.parquet"

        restamp_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )

        src_rg = pq.ParquetFile(src).metadata.row_group(0).column(0)
        dst_rg = pq.ParquetFile(dst).metadata.row_group(0).column(0)
        assert dst_rg.compression == src_rg.compression

    def test_no_temp_file_survives(self, catalog_root):
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 20)
        dst = catalog_root / "out.parquet"

        restamp_file(
            _only_file(src_dir),
            dst,
            new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL",
            new_instrument_id="AAA.ARCA",
        )

        assert list(catalog_root.glob("*.tmp")) == []

    def test_unreadable_source_fails_without_raising(self, catalog_root):
        bad = catalog_root / "bad.parquet"
        bad.write_bytes(b"not a parquet file")

        with pytest.raises(Exception):
            # pq.ParquetFile itself raises on open; the executor catches it.
            restamp_file(
                bad,
                catalog_root / "out.parquet",
                new_bar_type="A.B-1-DAY-LAST-EXTERNAL",
                new_instrument_id="A.B",
            )


class TestVerification:
    """The checks that must fail when something is wrong."""

    def _restamped_pair(self, catalog_root):
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 60)
        src = _only_file(src_dir)
        dst = catalog_root / "out.parquet"
        restamp_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )
        return src, dst

    def test_verify_passes_on_a_good_rewrite(self, catalog_root):
        src, dst = self._restamped_pair(catalog_root)
        assert (
            verify_file(
                src,
                dst,
                new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL",
                new_instrument_id="AAA.ARCA",
            )
            is None
        )
        assert verify_row_groups(src, dst, deep=True) is None

    def test_verify_catches_a_missing_restamp(self, catalog_root):
        """A plain copy — the exact failure a rename-only implementation produces."""
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 20)
        src = _only_file(src_dir)
        dst = catalog_root / "copy.parquet"
        dst.write_bytes(src.read_bytes())

        error = verify_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )

        assert error is not None and "bar_type not restamped" in error

    def test_verify_catches_a_truncated_destination(self, catalog_root):
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 60)
        src = _only_file(src_dir)
        short_dir = _write_bars(catalog_root, "AAA.ARCA", "1-DAY-LAST", 10)
        dst = catalog_root / "short.parquet"
        restamp_file(
            _only_file(short_dir),
            dst,
            new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL",
            new_instrument_id="AAA.ARCA",
        )

        error = verify_file(
            src, dst, new_bar_type="AAA.ARCA-1-DAY-LAST-EXTERNAL", new_instrument_id="AAA.ARCA"
        )

        assert error is not None and "row count changed" in error

    def test_row_group_check_catches_value_drift_at_identical_size(self, catalog_root):
        """Same row count, same layout, different values — a row count would pass."""
        src = _only_file(_write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 40))
        other = _only_file(
            _write_bars(catalog_root, "BBB.AMEX", "1-DAY-LAST", 40, price_offset=500)
        )

        error = verify_row_groups(src, other)

        assert error is not None and "statistics changed" in error

    def test_row_group_check_catches_a_size_change(self, catalog_root):
        src = _only_file(_write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 40))
        other = _only_file(_write_bars(catalog_root, "BBB.AMEX", "1-DAY-LAST", 25))

        error = verify_row_groups(src, other)

        assert error is not None


class TestExecuteAction:
    """Partition-level orchestration and its resume states."""

    def test_happy_path_moves_and_removes_the_original(self, catalog_root):
        _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 30)
        action = _plan_one(catalog_root, "AAA", "ARCA")

        ok, detail = execute_action(action, deep_verify=True)

        assert (ok, detail) == (True, "ok")
        assert action.dst_dir.exists()
        assert not action.src_dir.exists()
        assert (
            pq.ParquetFile(_only_file(action.dst_dir)).schema_arrow.metadata[b"instrument_id"]
            == b"AAA.ARCA"
        )

    def test_filename_is_preserved(self, catalog_root):
        """The catalog derives its time range from the filename."""
        src_dir = _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 30)
        original_name = _only_file(src_dir).name
        action = _plan_one(catalog_root, "AAA", "ARCA")

        execute_action(action)

        assert _only_file(action.dst_dir).name == original_name

    def test_interrupted_state_is_detected_and_redone(self, catalog_root):
        """src+dst means a previous run died mid-copy; the partial dst is junk."""
        _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 30)
        action = _plan_one(catalog_root, "AAA", "ARCA")
        action.dst_dir.mkdir(parents=True)
        (action.dst_dir / "half-written.parquet").write_bytes(b"garbage")

        assert classify_action_state(action) is ActionState.INTERRUPTED
        ok, _ = execute_action(action)

        assert ok
        assert not (action.dst_dir / "half-written.parquet").exists()
        assert not action.src_dir.exists()

    def test_completed_action_is_skipped(self, catalog_root):
        _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 30)
        action = _plan_one(catalog_root, "AAA", "ARCA")
        execute_action(action)

        assert classify_action_state(action) is ActionState.DONE
        assert execute_action(action) == (True, "already done")

    def test_missing_both_sides_aborts_rather_than_guessing(self, catalog_root):
        _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 30)
        action = _plan_one(catalog_root, "AAA", "ARCA")
        import shutil

        shutil.rmtree(action.src_dir)

        assert classify_action_state(action) is ActionState.MISSING
        ok, detail = execute_action(action)
        assert ok is False
        assert "neither source nor destination" in detail

    def test_source_survives_a_failed_verification(self, catalog_root):
        """Nothing is deleted until every file has been written and checked."""
        _write_bars(catalog_root, "AAA.AMEX", "1-DAY-LAST", 30)
        action = _plan_one(catalog_root, "AAA", "ARCA")
        corrupt = action.src_dir / "extra.parquet"
        corrupt.write_bytes(b"not parquet")
        action = type(action)(**{**action.__dict__, "files": action.files + (corrupt,)})

        ok, detail = execute_action(action)

        assert ok is False
        assert action.src_dir.exists(), "source must survive a failed run"
