"""Component tests for ZipExtractor — full extract → hand-off → cleanup loop.

Exercises the per-archive extraction stage (Story 2.2 / ADR-8) end-to-end with
**real** temporary ZIP fixtures (stdlib ``zipfile``) and a recording stub
handler standing in for the not-yet-implemented parser hand-off. No network,
no live data, no Nautilus.

Acceptance-criteria coverage:

- AC1 — one archive at a time + cleanup before the next archive.
- AC2 — a mid-archive failure (handler raise / corrupt ZIP) leaves no stray
  ``.txt`` files and the exception propagates (not swallowed).
- AC3 — archive-granular resume: completed archives skipped via an injected
  observable-state predicate, no ledger file written by the extractor.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.services.firstrate.zip_extractor import (
    ArchiveBatch,
    ArchiveResult,
    ZipExtractor,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_zip(path: Path, members: dict[str, str]) -> Path:
    """Write a real ZIP at ``path`` containing ``{arcname: text}`` members."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, text in members.items():
            zf.writestr(arcname, text)
    return path


@pytest.fixture()
def archives(tmp_path: Path) -> list[Path]:
    """Two real letter-batched ZIPs: A (2 tickers), B (1 ticker)."""
    src = tmp_path / "source"
    src.mkdir()
    a = _make_zip(
        src / "A.zip",
        {
            "AAPL_full_1day_adjsplitdiv.txt": "2020-01-01,1,2,0.5,1.5,100",
            "ARKK_full_1day_adjsplitdiv.txt": "2020-01-01,5,6,4,5.5,50",
        },
    )
    b = _make_zip(
        src / "B.zip",
        {"BAC_full_1day_adjsplitdiv.txt": "2020-01-01,3,4,2,3.5,75"},
    )
    return [a, b]


class RecordingHandler:
    """Parser hand-off stub: records what it observes at each call."""

    def __init__(self) -> None:
        self.batches: list[ArchiveBatch] = []
        # Per call: which previously-seen staging dirs still exist on disk.
        self.other_dirs_alive_at_call: list[list[Path]] = []
        # Per call: the files that actually existed when the handler ran.
        self.files_present_at_call: list[list[Path]] = []
        self._seen_dirs: list[Path] = []

    def __call__(self, batch: ArchiveBatch) -> None:
        alive = [d for d in self._seen_dirs if d.exists()]
        self.other_dirs_alive_at_call.append(alive)
        self.files_present_at_call.append([f for f in batch.txt_files if f.exists()])
        self.batches.append(batch)
        self._seen_dirs.append(batch.staging_dir)


# ---------------------------------------------------------------------------
# AC1 — one archive at a time + cleanup before next
# ---------------------------------------------------------------------------


def test_extracts_one_archive_at_a_time_with_cleanup(archives: list[Path]) -> None:
    handler = RecordingHandler()

    results = ZipExtractor().extract_archives(archives, handler)

    # Handler invoked once per archive, in input order.
    assert [b.archive for b in handler.batches] == archives
    assert all(isinstance(r, ArchiveResult) for r in results)
    assert [r.status for r in results] == ["extracted", "extracted"]
    assert [r.txt_count for r in results] == [2, 1]

    # At each hand-off, exactly the current archive's .txt files exist...
    assert [len(f) for f in handler.files_present_at_call] == [2, 1]
    # ...and no earlier archive's staging dir is still on disk (one at a time).
    assert handler.other_dirs_alive_at_call == [[], []]

    # After the run, every staging dir has been removed (peak = one batch).
    assert all(not b.staging_dir.exists() for b in handler.batches)
    assert all(not f.exists() for b in handler.batches for f in b.txt_files)


def test_only_txt_members_are_extracted(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    archive = _make_zip(
        src / "A.zip",
        {
            "AAPL_full_1day_adjsplitdiv.txt": "2020-01-01,1,2,0.5,1.5,100",
            "README.md": "not data",
            "notes.csv": "1,2,3",
        },
    )
    handler = RecordingHandler()

    ZipExtractor().extract_archives([archive], handler)

    (batch,) = handler.batches
    assert [f.name for f in batch.txt_files] == ["AAPL_full_1day_adjsplitdiv.txt"]


# ---------------------------------------------------------------------------
# AC2 — interruption leaves no stray files + clean failure
# ---------------------------------------------------------------------------


def test_handler_failure_leaves_no_stray_files_and_propagates(
    archives: list[Path], tmp_path: Path
) -> None:
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    captured: dict[str, Path] = {}

    def exploding_handler(batch: ArchiveBatch) -> None:
        # Files are real on disk at hand-off time...
        assert all(f.exists() for f in batch.txt_files)
        captured["staging_dir"] = batch.staging_dir
        raise RuntimeError("simulated mid-archive interruption")

    extractor = ZipExtractor(staging_root=staging_root)

    with pytest.raises(RuntimeError, match="simulated mid-archive interruption"):
        extractor.extract_archives(archives, exploding_handler)

    # No stray extracted files anywhere under the staging root.
    assert not captured["staging_dir"].exists()
    assert list(staging_root.rglob("*.txt")) == []
    # The failing archive is left not-complete (only one was attempted).


def test_corrupt_archive_propagates_and_leaves_no_stray_files(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    bad = src / "A.zip"
    bad.write_bytes(b"this is not a valid zip archive")

    handler = RecordingHandler()
    extractor = ZipExtractor(staging_root=staging_root)

    with pytest.raises(zipfile.BadZipFile):
        extractor.extract_archives([bad], handler)

    # Hand-off never happened; nothing left behind.
    assert handler.batches == []
    assert list(staging_root.rglob("*.txt")) == []
    assert list(staging_root.iterdir()) == []


# ---------------------------------------------------------------------------
# AC3 — archive-granular resume via observable-state predicate
# ---------------------------------------------------------------------------


def test_resume_skips_completed_archives_without_ledger(
    archives: list[Path], tmp_path: Path
) -> None:
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    a, b = archives

    # Observable state: pretend archive A was already completed on a prior run.
    completed = {a}

    handler = RecordingHandler()
    extractor = ZipExtractor(staging_root=staging_root)

    results = extractor.extract_archives(
        archives, handler, is_complete=lambda arc: arc in completed
    )

    # A skipped (no hand-off), B extracted.
    assert [r.status for r in results] == ["skipped", "extracted"]
    assert [b_.archive for b_ in handler.batches] == [b]

    # The extractor wrote no ledger / state file of its own.
    assert list(staging_root.iterdir()) == []


def test_full_run_when_no_predicate_supplied(archives: list[Path]) -> None:
    handler = RecordingHandler()
    results = ZipExtractor().extract_archives(archives, handler)
    assert [r.status for r in results] == ["extracted", "extracted"]
    assert len(handler.batches) == 2
