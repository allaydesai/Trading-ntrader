"""Selective extraction of Kraken's split OHLCVT archive (crypto M7, task 7.7).

The full archive is ~9.5 GB in five ``.zip.partNN`` files whose concatenation is one ZIP.
:class:`SplitArchive` presents the parts as one seekable file read with HTTP ``Range``
requests, so only the central directory and the wanted members are fetched. The tests use an
in-memory fetcher over a real ZIP split at arbitrary byte boundaries.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from src.services.crypto.kraken_ohlcvt_download import (
    SplitArchive,
    extract_pairs,
    wanted_members,
)


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buf.getvalue()


class _FakeRemote:
    """Serves a byte string split into parts; records every ranged read."""

    def __init__(self, blob: bytes, cuts: list[int]) -> None:
        bounds = [0, *cuts, len(blob)]
        self.parts = {
            f"https://x/part{i:02d}": blob[a:b] for i, (a, b) in enumerate(zip(bounds, bounds[1:]))
        }
        self.reads: list[tuple[str, int, int]] = []

    def size(self, url: str) -> int:
        return len(self.parts[url])

    def read(self, url: str, start: int, end: int) -> bytes:
        self.reads.append((url, start, end))
        return self.parts[url][start : end + 1]


MEMBERS = {
    "XBTUSD_1.csv": b"1381095240,122.0,122.0,122.0,122.0,0.1,1\n" * 50,
    "XBTUSD_60.csv": b"1381093200,122.0,123.0,121.0,122.5,3.5,7\n",
    "XBTUSDT_1.csv": b"1600000000,1,1,1,1,1,1\n",
    "SOLUSD_1440.csv": b"1622592000,35.1,36.0,34.0,35.5,1000,99\n",
    "DOGEUSD_1.csv": b"1600000000,0.1,0.1,0.1,0.1,1,1\n" * 2000,
    "MANIFEST.json": json.dumps({"through": "2026-09-30"}).encode(),
}


@pytest.fixture
def remote():
    blob = _zip_bytes(MEMBERS)
    return _FakeRemote(blob, cuts=[97, len(blob) // 2, len(blob) - 40])


def test_the_parts_read_as_one_file_across_part_boundaries(remote):
    archive = SplitArchive(sorted(remote.parts), remote)
    blob = b"".join(remote.parts[u] for u in sorted(remote.parts))
    assert archive.size == len(blob)
    archive.seek(90)
    assert archive.read(20) == blob[90:110]  # spans part00 -> part01
    archive.seek(-5, io.SEEK_END)
    assert archive.read(100) == blob[-5:]
    assert archive.read(1) == b""


def test_wanted_members_are_the_altname_files_of_the_chosen_intervals():
    names = list(MEMBERS)
    assert wanted_members(names, ["XBTUSD", "SOLUSD"], [1, 1440]) == [
        "SOLUSD_1440.csv",
        "XBTUSD_1.csv",
    ]  # XBTUSDT_1 is another pair, not a prefix match


def test_extract_writes_only_the_wanted_members_and_a_manifest(remote, tmp_path: Path):
    archive = SplitArchive(sorted(remote.parts), remote)
    written = extract_pairs(archive, tmp_path, ["XBTUSD"], [1, 60])
    assert sorted(p.name for p in written) == ["XBTUSD_1.csv", "XBTUSD_60.csv"]
    assert (tmp_path / "XBTUSD_1.csv").read_bytes() == MEMBERS["XBTUSD_1.csv"]
    assert not (tmp_path / "DOGEUSD_1.csv").exists()
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text())
    assert manifest == {"through": "2026-09-30"}  # the archive's own build manifest
    sums = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert [line.split()[1] for line in sums] == ["XBTUSD_1.csv", "XBTUSD_60.csv"]


def test_extract_refuses_to_overwrite_an_existing_raw_file(remote, tmp_path: Path):
    (tmp_path / "XBTUSD_60.csv").write_text("kept")
    archive = SplitArchive(sorted(remote.parts), remote)
    with pytest.raises(FileExistsError, match="XBTUSD_60.csv"):
        extract_pairs(archive, tmp_path, ["XBTUSD"], [60])
    assert (tmp_path / "XBTUSD_60.csv").read_text() == "kept"


def test_a_pair_missing_from_the_archive_is_an_error(remote, tmp_path: Path):
    archive = SplitArchive(sorted(remote.parts), remote)
    with pytest.raises(ValueError, match="ETHUSD"):
        extract_pairs(archive, tmp_path, ["XBTUSD", "ETHUSD"], [1])


def test_two_members_with_one_name_are_refused_before_writing(tmp_path: Path):
    """Review follow-up: a/XBTUSD_60.csv and b/XBTUSD_60.csv would overwrite each other."""
    blob = _zip_bytes(
        {"a/XBTUSD_60.csv": b"1,1,1,1,1,1,1\n", "b/XBTUSD_60.csv": b"2,2,2,2,2,2,2\n"}
    )
    remote = _FakeRemote(blob, cuts=[50])
    archive = SplitArchive(sorted(remote.parts), remote)
    with pytest.raises(ValueError, match="XBTUSD_60.csv"):
        extract_pairs(archive, tmp_path, ["XBTUSD"], [60])
    assert not list(tmp_path.iterdir())


def test_an_interrupted_download_publishes_nothing_and_can_be_rerun(remote, tmp_path, monkeypatch):
    """CodeRabbit #77: a failure on the second member must not leave the first published."""
    import shutil

    from src.services.crypto import kraken_ohlcvt_download as download

    real_copy, calls = shutil.copyfileobj, []

    def flaky(source, sink, length=0):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("connection reset")
        return real_copy(source, sink, length)

    monkeypatch.setattr(download.shutil, "copyfileobj", flaky)
    archive = SplitArchive(sorted(remote.parts), remote)
    with pytest.raises(OSError, match="connection reset"):
        extract_pairs(archive, tmp_path, ["XBTUSD"], [1, 60])
    assert not (tmp_path / "XBTUSD_1.csv").exists() and not (tmp_path / "XBTUSD_60.csv").exists()

    written = extract_pairs(
        SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [1, 60]
    )
    assert sorted(p.name for p in written) == ["XBTUSD_1.csv", "XBTUSD_60.csv"]
    assert not list(tmp_path.glob("*.partial"))


def test_a_rerun_completes_files_an_interrupted_publish_left_behind(remote, tmp_path):
    """CodeRabbit #77: a crash between renames leaves some files published. A rerun takes a
    published file whose bytes match the archive member as done; a different file still blocks."""
    (tmp_path / "XBTUSD_1.csv").write_bytes(MEMBERS["XBTUSD_1.csv"])  # published, then crash
    written = extract_pairs(
        SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [1, 60]
    )
    assert sorted(p.name for p in written) == ["XBTUSD_1.csv", "XBTUSD_60.csv"]
    assert (tmp_path / "XBTUSD_60.csv").read_bytes() == MEMBERS["XBTUSD_60.csv"]
    sums = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert sorted(line.split()[1] for line in sums) == ["XBTUSD_1.csv", "XBTUSD_60.csv"]
    again = extract_pairs(SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [1, 60])
    assert len(again) == 2
    assert len((tmp_path / "SHA256SUMS").read_text().splitlines()) == 2  # no duplicate lines


def _verifiable(sums: Path) -> dict[str, str]:
    """Each line is ``<64 hex>  <name>`` and its hash matches the file beside it."""
    import hashlib
    import re

    text = sums.read_text()
    assert text.endswith("\n")
    records = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
        assert match, f"malformed SHA256SUMS line: {line!r}"
        digest, name = match.groups()
        assert hashlib.sha256((sums.parent / name).read_bytes()).hexdigest() == digest
        records[name] = digest
    return records


def test_a_checksum_record_cut_off_before_its_newline_is_repaired(remote, tmp_path):
    """CodeRabbit #77: an append interrupted before its newline must not glue the next record
    onto it; the rerun rewrites the whole file, every line separated and verifiable."""
    import hashlib

    published = MEMBERS["XBTUSD_1.csv"]
    (tmp_path / "XBTUSD_1.csv").write_bytes(published)
    digest = hashlib.sha256(published).hexdigest()
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  XBTUSD_1.csv")  # no final newline
    extract_pairs(SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [1, 60])
    assert sorted(_verifiable(tmp_path / "SHA256SUMS")) == ["XBTUSD_1.csv", "XBTUSD_60.csv"]


def test_a_truncated_checksum_record_is_replaced_not_trusted(remote, tmp_path):
    """A half-written record (hash cut short) does not count as listing its file."""
    (tmp_path / "XBTUSD_1.csv").write_bytes(MEMBERS["XBTUSD_1.csv"])
    (tmp_path / "SHA256SUMS").write_text("3f2a9c")  # crash mid-hash
    extract_pairs(SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [1, 60])
    assert sorted(_verifiable(tmp_path / "SHA256SUMS")) == ["XBTUSD_1.csv", "XBTUSD_60.csv"]


def test_manifest_and_checksums_are_published_by_atomic_replace(remote, tmp_path, monkeypatch):
    """Both files are written to a temp file in the same directory, then ``os.replace``d over
    the target, so a crash leaves the old file or the new one, never a partial one."""
    from src.services.crypto import kraken_ohlcvt_download as download

    replaced: list[tuple[Path, Path]] = []
    real_replace = download.os.replace

    def spy(src, dst):
        replaced.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(download.os, "replace", spy)
    extract_pairs(SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [60])
    assert sorted(dst.name for _, dst in replaced) == ["MANIFEST.json", "SHA256SUMS"]
    assert all(src.parent == dst.parent == tmp_path for src, dst in replaced)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "MANIFEST.json",
        "SHA256SUMS",
        "XBTUSD_60.csv",
    ]


def test_a_failed_checksum_publish_keeps_the_old_file_and_no_temp(remote, tmp_path, monkeypatch):
    from src.services.crypto import kraken_ohlcvt_download as download

    (tmp_path / "SHA256SUMS").write_text("old\n")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(download.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        extract_pairs(SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [60])
    assert (tmp_path / "SHA256SUMS").read_text() == "old\n"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_atomically_published_files_keep_the_umask_mode(remote, tmp_path):
    """A temp file is created 0600; the published files get the usual umask mode, like the CSVs."""
    import os
    import stat

    extract_pairs(SplitArchive(sorted(remote.parts), remote), tmp_path, ["XBTUSD"], [60])
    umask = os.umask(0)
    os.umask(umask)
    for name in ("MANIFEST.json", "SHA256SUMS"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o666 & ~umask
