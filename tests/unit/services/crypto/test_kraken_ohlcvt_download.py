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
