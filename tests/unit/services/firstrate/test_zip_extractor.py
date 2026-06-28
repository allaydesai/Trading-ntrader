"""Unit tests for ZipExtractor pure-logic edges (Story 2.2).

Covers member filtering, zip-slip flattening, empty-archive handling, input
ordering, archive discovery, and the dataclass shapes — all stdlib, no
Nautilus, no network.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.services.firstrate.zip_extractor import (
    ArchiveBatch,
    ArchiveResult,
    ZipExtractor,
    discover_archives,
)


def _make_zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for arcname, text in members.items():
            zf.writestr(arcname, text)
    return path


def test_archive_batch_and_result_shape(tmp_path: Path) -> None:
    batch = ArchiveBatch(archive=tmp_path / "A.zip", txt_files=(), staging_dir=tmp_path)
    assert batch.archive.name == "A.zip"
    assert batch.txt_files == ()
    result = ArchiveResult(archive=tmp_path / "A.zip", status="extracted", txt_count=3)
    assert result.status == "extracted"
    assert result.txt_count == 3


def test_zip_slip_member_is_flattened_to_basename(tmp_path: Path) -> None:
    """A malicious ``../`` member must not escape the staging dir."""
    archive = _make_zip(
        tmp_path / "evil.zip",
        {"../../escape_full_1day_adjsplitdiv.txt": "2020-01-01,1,2,0.5,1.5,1"},
    )
    seen: list[Path] = []
    ZipExtractor().extract_archives([archive], lambda b: seen.extend(b.txt_files))
    (extracted,) = seen
    # Flattened to a basename living *inside* the staging dir, not outside it.
    assert extracted.name == "escape_full_1day_adjsplitdiv.txt"
    assert ".." not in extracted.parts


def test_empty_archive_hands_off_empty_batch(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "A.zip", {"README.md": "no data here"})
    batches: list[ArchiveBatch] = []
    results = ZipExtractor().extract_archives([archive], batches.append)
    (batch,) = batches
    assert batch.txt_files == ()
    assert results[0].status == "extracted"
    assert results[0].txt_count == 0


def test_input_order_is_preserved(tmp_path: Path) -> None:
    z1 = _make_zip(tmp_path / "C.zip", {"C_full_1day_adjsplitdiv.txt": "x"})
    z2 = _make_zip(tmp_path / "A.zip", {"A_full_1day_adjsplitdiv.txt": "x"})
    z3 = _make_zip(tmp_path / "B.zip", {"B_full_1day_adjsplitdiv.txt": "x"})
    order = [z1, z2, z3]
    seen: list[Path] = []
    ZipExtractor().extract_archives(order, lambda b: seen.append(b.archive))
    assert seen == order


def test_discover_archives_returns_sorted_zips(tmp_path: Path) -> None:
    _make_zip(tmp_path / "C.zip", {"x.txt": "x"})
    _make_zip(tmp_path / "A.zip", {"x.txt": "x"})
    _make_zip(tmp_path / "B.zip", {"x.txt": "x"})
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "sub").mkdir()
    found = discover_archives(tmp_path)
    assert [p.name for p in found] == ["A.zip", "B.zip", "C.zip"]


def test_discover_archives_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_archives(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Code-review hardening (collision / backslash / staging-root)
# ---------------------------------------------------------------------------


def test_duplicate_basename_raises_and_leaves_no_stray_files(tmp_path: Path) -> None:
    """Two members flattening to the same basename must not silently overwrite."""
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    archive = _make_zip(
        tmp_path / "A.zip",
        {
            "a/SPY_full_1day_adjsplitdiv.txt": "1",
            "b/SPY_full_1day_adjsplitdiv.txt": "2",
        },
    )
    batches: list[ArchiveBatch] = []
    extractor = ZipExtractor(staging_root=staging_root)
    with pytest.raises(ValueError, match="Duplicate .txt basename"):
        extractor.extract_archives([archive], batches.append)
    # Failure surfaces before hand-off and leaves nothing behind.
    assert batches == []
    assert list(staging_root.rglob("*.txt")) == []


def test_backslash_separator_member_is_flattened(tmp_path: Path) -> None:
    """Windows-style backslash separators must flatten to a clean basename."""
    archive = _make_zip(
        tmp_path / "A.zip",
        {"A\\AAPL_full_1day_adjsplitdiv.txt": "2020-01-01,1,2,0.5,1.5,1"},
    )
    seen: list[Path] = []
    ZipExtractor().extract_archives([archive], lambda b: seen.extend(b.txt_files))
    (extracted,) = seen
    assert extracted.name == "AAPL_full_1day_adjsplitdiv.txt"
    assert "\\" not in extracted.name


def test_missing_staging_root_is_created(tmp_path: Path) -> None:
    """A staging_root that does not yet exist is created, not failed late."""
    staging_root = tmp_path / "nested" / "staging"  # parent does not exist
    archive = _make_zip(tmp_path / "A.zip", {"A_full_1day_adjsplitdiv.txt": "x"})
    batches: list[ArchiveBatch] = []
    ZipExtractor(staging_root=staging_root).extract_archives([archive], batches.append)
    assert staging_root.exists()
    assert len(batches) == 1
