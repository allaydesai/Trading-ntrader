"""Fetch chosen pairs from Kraken's downloadable OHLCVT history (crypto M7).

Kraken publishes every spot pair's candle history as CSV (support article "Downloadable
historical OHLCVT (Open, High, Low, Close, Volume, Trades) data",
<https://support.kraken.com/articles/360047124832>). The complete archive (each pair from its
first trade through 30 September 2026, read 2026-11-24) is one ~9.5 GB ZIP split into five
``Kraken_OHLCVT_Full.zip.partNN`` files whose concatenation is the ZIP; quarterly archives
(``Kraken_OHLCVT_2026Q3.zip``) carry the increments.

Three coins need about 210 MB of it, so :class:`SplitArchive` presents the parts as one
seekable file read with HTTP ``Range`` requests (the host sends ``Accept-Ranges: bytes``) and
:func:`extract_pairs` pulls only the wanted members: ``{ALTNAME}_{minutes}.csv``. No login, no
API key. Files are written once: an existing raw file is never overwritten (raw data on the VPS
is append-only). The archive's own ``MANIFEST.json`` and a ``SHA256SUMS`` of the written files
sit beside them; both are replaced atomically (temp file + ``os.replace``).

Usage::

    uv run python -m src.services.crypto.kraken_ohlcvt_download \\
        --dest /srv/market-data/raw/kraken/ohlcvt XBTUSD ETHUSD SOLUSD
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

BASE_URL = "https://assets.kraken.com/marketing/institutions/"
FULL_PARTS = tuple(f"Kraken_OHLCVT_Full.zip.part{i:02d}" for i in range(5))
#: Kraken interval (minutes) -> the catalog timeframe it fills: the one list of what a Kraken
#: import covers (the importer, ``import --format kraken`` and this download all read it).
#: Kraken also ships 15, 240 and 720, which have no catalog coverage field.
INTERVAL_TIMEFRAMES = {
    1: "1-MINUTE-LAST",
    5: "5-MINUTE-LAST",
    60: "1-HOUR-LAST",
    1440: "1-DAY-LAST",
}
CATALOG_INTERVALS = tuple(INTERVAL_TIMEFRAMES)
MANIFEST = "MANIFEST.json"
CHECKSUMS = "SHA256SUMS"
_USER_AGENT = "ntrader-backtester"
_COPY_CHUNK = 1 << 20
#: Read-ahead per ranged request: large enough that a 90 MB member takes ~12 requests.
_READ_AHEAD = 8 << 20


class RangeFetcher(Protocol):
    """Reads byte ranges of remote files (HTTP in production, in memory in tests)."""

    def size(self, url: str) -> int: ...  # noqa: D102

    def read(self, url: str, start: int, end: int) -> bytes: ...  # noqa: D102 — end inclusive


class HttpRangeFetcher:
    """``HEAD`` for sizes, ``GET`` with ``Range: bytes=a-b`` for reads (public URLs only)."""

    def __init__(self, timeout: float = 120.0) -> None:
        self.timeout = timeout

    def _open(self, url: str, method: str = "GET", headers: dict[str, str] | None = None):
        request = urllib.request.Request(
            url, method=method, headers={"User-Agent": _USER_AGENT, **(headers or {})}
        )
        return urllib.request.urlopen(request, timeout=self.timeout)  # noqa: S310 — https only

    def size(self, url: str) -> int:  # noqa: D102
        with self._open(url, method="HEAD") as response:
            return int(response.headers["Content-Length"])

    def read(self, url: str, start: int, end: int) -> bytes:  # noqa: D102
        with self._open(url, headers={"Range": f"bytes={start}-{end}"}) as response:
            if response.status != 206:
                raise OSError(f"{url} ignored the Range request (HTTP {response.status})")
            return response.read()


class SplitArchive(io.RawIOBase):
    """The concatenation of remote ``parts`` as one read-only, seekable file."""

    def __init__(self, parts: Sequence[str], fetcher: RangeFetcher) -> None:
        super().__init__()
        self._fetcher = fetcher
        self._parts: list[tuple[int, int, str]] = []  # (offset, size, url)
        offset = 0
        for url in parts:
            size = fetcher.size(url)
            self._parts.append((offset, size, url))
            offset += size
        self.size = offset
        self._pos = 0

    def readable(self) -> bool:  # noqa: D102
        return True

    def seekable(self) -> bool:  # noqa: D102
        return True

    def tell(self) -> int:  # noqa: D102
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:  # noqa: D102
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def readinto(self, buffer) -> int:  # noqa: D102 — one ranged read per part touched
        view = memoryview(buffer).cast("B")
        filled = 0
        for offset, size, url in self._parts:
            want = min(len(view) - filled, self.size - self._pos)
            if want <= 0:
                break
            if not offset <= self._pos < offset + size:
                continue
            start = self._pos - offset
            data = self._fetcher.read(url, start, min(start + want, size) - 1)
            if not data:
                break
            view[filled : filled + len(data)] = data
            filled += len(data)
            self._pos += len(data)
        return filled


def wanted_members(names: Iterable[str], altnames: Sequence[str], intervals: Sequence[int]):
    """The archive members ``{ALTNAME}_{minutes}.csv`` for the chosen pairs, sorted."""
    targets = {f"{alt}_{minutes}.csv" for alt in altnames for minutes in intervals}
    return sorted(name for name in names if Path(name).name in targets)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_COPY_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pairs(
    archive_file: io.RawIOBase,
    dest: Path,
    altnames: Sequence[str],
    intervals: Sequence[int] = CATALOG_INTERVALS,
) -> list[Path]:
    """Write each pair's interval files (and the archive manifest) to ``dest``; return them.

    Raises:
        ValueError: A pair has no file for one of ``intervals`` in the archive.
        FileExistsError: A target file exists with other contents (raw data is never
            overwritten; one equal to the archive member, left by an interrupted run, is kept).
    """
    archive = zipfile.ZipFile(io.BufferedReader(archive_file, buffer_size=_READ_AHEAD))  # type: ignore[arg-type]
    members = wanted_members(archive.namelist(), altnames, intervals)
    found = {Path(name).name for name in members}
    missing = [
        f"{alt}_{m}.csv" for alt in altnames for m in intervals if f"{alt}_{m}.csv" not in found
    ]
    if missing:
        raise ValueError(f"Not in the Kraken archive: {', '.join(missing)}")
    counts = Counter(Path(name).name for name in members)
    twice = sorted(name for name, n in counts.items() if n > 1)
    if twice:  # two folders with one file name would overwrite each other
        raise ValueError(f"The archive holds more than one {', '.join(twice)}; extract by hand")
    targets = [dest / Path(name).name for name in members]
    # A file a previous run published (it equals the archive member: an interrupted publish)
    # counts as done; any other existing file is refused.
    done = {t for name, t in zip(members, targets) if t.exists() and _same(archive, name, t)}
    clashes = [t.name for t in targets if t.exists() and t not in done]
    if clashes:
        raise FileExistsError(f"Refusing to overwrite raw files in {dest}: {', '.join(clashes)}")
    dest.mkdir(parents=True, exist_ok=True)
    todo = [(name, t) for name, t in zip(members, targets) if t not in done]
    # Stage every member before publishing any, so a failed download leaves only .partial
    # files (overwritten by a rerun) and never a published file that blocks the rerun.
    partials = [t.with_suffix(t.suffix + ".partial") for _, t in todo]
    for (name, _), partial in zip(todo, partials):
        with archive.open(name) as source, partial.open("wb") as sink:
            shutil.copyfileobj(source, sink, _COPY_CHUNK)
    for partial, (_, target) in zip(partials, todo):
        partial.rename(target)
    manifest = next((n for n in archive.namelist() if Path(n).name == MANIFEST), None)
    if manifest is not None and not (dest / MANIFEST).exists():
        _write_atomic(dest / MANIFEST, archive.read(manifest))
    _record_checksums(dest / CHECKSUMS, targets)
    return targets


def _same(archive: zipfile.ZipFile, member: str, path: Path) -> bool:
    """``path`` holds exactly the archive member (size and CRC-32, read in chunks)."""
    info = archive.getinfo(member)
    if path.stat().st_size != info.file_size:
        return False
    crc = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_COPY_CHUNK), b""):
            crc = zlib.crc32(block, crc)
    return crc == info.CRC


_RECORD = re.compile(r"[0-9a-f]{64}  (\S+)")


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` via a temp file in its directory and ``os.replace``.

    A crash leaves the old file or the new one, never a partial one, and no temp file behind.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)  # mkstemp's 0600 would outlive the replace
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _record_checksums(sums: Path, targets: Sequence[Path]) -> None:
    """Add a ``SHA256SUMS`` line for each target the file does not list yet.

    The whole file is rewritten atomically. Only well-formed records (``<sha256>  <name>``) are
    kept, so one cut short by a crash before this fix never hides a file or glues onto the next.
    """
    old = sums.read_text().splitlines() if sums.exists() else []
    kept = [line for line in old if _RECORD.fullmatch(line)]
    listed = {line.split("  ", 1)[1] for line in kept}
    added = [f"{_sha256(t)}  {t.name}" for t in targets if t.name not in listed]
    _write_atomic(sums, "".join(f"{line}\n" for line in [*kept, *added]).encode())


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m src.services.crypto.kraken_ohlcvt_download --dest DIR ALTNAME...``."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, required=True, help="raw directory to write to")
    parser.add_argument("altnames", nargs="+", help="Kraken altnames, e.g. XBTUSD ETHUSD")
    parser.add_argument(
        "--intervals",
        default=",".join(map(str, CATALOG_INTERVALS)),
        help="comma-separated minutes (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    intervals = [int(v) for v in args.intervals.split(",")]
    archive = SplitArchive([BASE_URL + part for part in FULL_PARTS], HttpRangeFetcher())
    for path in extract_pairs(archive, args.dest, args.altnames, intervals):
        print(f"{path}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
