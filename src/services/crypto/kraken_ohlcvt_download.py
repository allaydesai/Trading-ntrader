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
sit beside them.

Usage::

    uv run python -m src.services.crypto.kraken_ohlcvt_download \\
        --dest /srv/market-data/raw/kraken/ohlcvt XBTUSD ETHUSD SOLUSD
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

BASE_URL = "https://assets.kraken.com/marketing/institutions/"
FULL_PARTS = tuple(f"Kraken_OHLCVT_Full.zip.part{i:02d}" for i in range(5))
#: The intervals (minutes) the ``kraken-crypto`` catalog holds: 1-MINUTE, 5-MINUTE, 1-HOUR,
#: 1-DAY. Kraken also ships 15, 240 and 720, which have no catalog coverage field.
CATALOG_INTERVALS = (1, 5, 60, 1440)
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
        FileExistsError: A target file already exists (raw data is never overwritten).
    """
    archive = zipfile.ZipFile(io.BufferedReader(archive_file, buffer_size=_READ_AHEAD))  # type: ignore[arg-type]
    members = wanted_members(archive.namelist(), altnames, intervals)
    found = {Path(name).name for name in members}
    missing = [
        f"{alt}_{m}.csv" for alt in altnames for m in intervals if f"{alt}_{m}.csv" not in found
    ]
    if missing:
        raise ValueError(f"Not in the Kraken archive: {', '.join(missing)}")
    targets = [dest / Path(name).name for name in members]
    clashes = [t.name for t in targets if t.exists()]
    if clashes:
        raise FileExistsError(f"Refusing to overwrite raw files in {dest}: {', '.join(clashes)}")
    dest.mkdir(parents=True, exist_ok=True)
    for name, target in zip(members, targets):
        partial = target.with_suffix(target.suffix + ".partial")
        with archive.open(name) as source, partial.open("wb") as sink:
            shutil.copyfileobj(source, sink, _COPY_CHUNK)
        partial.rename(target)
    manifest = next((n for n in archive.namelist() if Path(n).name == MANIFEST), None)
    if manifest is not None and not (dest / MANIFEST).exists():
        (dest / MANIFEST).write_bytes(archive.read(manifest))
    lines = [f"{_sha256(t)}  {t.name}" for t in targets]
    with (dest / CHECKSUMS).open("a") as sums:
        sums.write("".join(f"{line}\n" for line in lines))
    return targets


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
