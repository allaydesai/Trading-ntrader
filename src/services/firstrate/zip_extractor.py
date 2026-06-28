"""Per-archive ZIP extraction stage for FirstRate imports (Story 2.2 / ADR-8).

Extracts FirstRate letter-batched ZIP archives **one at a time**, hands each
archive's extracted ``.txt`` files to an injected caller (the parser hand-off
seam), and deletes them before moving to the next archive — so peak on-disk
footprint stays bounded to a single archive's contents.

Consistency-on-interrupt (ADR-8): an interruption mid-archive (a corrupt
archive or a failing hand-off) leaves **no stray extracted files** behind and
surfaces the failure cleanly to the caller (the exception propagates — it is
never swallowed). A re-run resumes at **archive granularity** via an injected
observable-state predicate (``is_complete``); the extractor never writes its
own progress ledger, mirroring the metadata-as-source-of-truth discipline in
``import_service`` (ADR-5).

Stdlib only (``zipfile`` / ``pathlib`` / ``tempfile`` / ``shutil``). This
module MUST NOT import ``nautilus_trader`` or the parser: the hand-off is a
callback seam so the extractor stays Nautilus-free and unit-testable without
``--forked``. The later import wiring (Story 2.4) plugs ``FirstRateCsvParser``
+ catalog write in behind the ``handler`` seam.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ArchiveBatch:
    """One archive's extracted ``.txt`` files, handed to the parser seam.

    Attributes:
        archive: The source ZIP archive this batch was extracted from.
        txt_files: Extracted ``.txt`` paths (basename-flattened), sorted.
        staging_dir: The per-archive temp directory holding the files. It is
            deleted as soon as the hand-off returns (or raises) — do not retain
            references past the handler call.
    """

    archive: Path
    txt_files: tuple[Path, ...]
    staging_dir: Path


@dataclass(frozen=True)
class ArchiveResult:
    """Per-archive outcome for the run summary.

    Attributes:
        archive: The source ZIP archive.
        status: ``"extracted"`` (processed this run) or ``"skipped"`` (already
            complete per the resume predicate).
        txt_count: Number of ``.txt`` files handed to the caller (0 for skips).
    """

    archive: Path
    status: Literal["extracted", "skipped"]
    txt_count: int


#: The parser hand-off seam — invoked exactly once per extracted archive.
ArchiveHandler = Callable[[ArchiveBatch], None]
#: Observable-state resume predicate — truthy when an archive is already done.
CompletionPredicate = Callable[[Path], bool]


def discover_archives(source_dir: Path) -> list[Path]:
    """Return the ``.zip`` archives directly under ``source_dir``, name-sorted.

    Sorting by name yields the natural A→Z letter-batch order. Subdirectories
    and non-``.zip`` files are ignored. The caller may also assemble the
    archive list itself and pass it to :meth:`ZipExtractor.extract_archives`.

    Args:
        source_dir: Directory containing the letter-batched ZIP archives.

    Returns:
        Sorted list of archive paths.

    Raises:
        FileNotFoundError: If ``source_dir`` does not exist.
    """
    if not source_dir.exists():
        raise FileNotFoundError(f"Archive source directory does not exist: {source_dir}")
    return sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == ".zip")


class ZipExtractor:
    """Extracts FirstRate ZIP archives one at a time with per-archive cleanup.

    Args:
        staging_root: Optional directory under which per-archive temp staging
            dirs are created. Defaults to the system temp location. Useful for
            keeping extraction on the same volume as the catalog (and for
            tests asserting no stray files remain).
    """

    def __init__(self, *, staging_root: Path | None = None) -> None:
        self._staging_root = staging_root

    def extract_archives(
        self,
        archives: Sequence[Path],
        handler: ArchiveHandler,
        is_complete: CompletionPredicate | None = None,
    ) -> list[ArchiveResult]:
        """Process archives in order: skip-or-extract → hand off → clean up.

        For each archive (in input order):

        1. If ``is_complete(archive)`` is truthy, the archive is skipped
           (no extraction, no hand-off) and recorded as ``"skipped"`` — this
           is the archive-granular resume path (AC3).
        2. Otherwise its ``.txt`` members are extracted into a fresh
           per-archive temp dir, handed to ``handler`` exactly once, then the
           temp dir (and its files) is deleted before the next archive (AC1).

        Only one staging dir exists at any moment, so peak extracted footprint
        is bounded to a single archive. Any exception raised during extraction
        or the hand-off propagates to the caller **after** the staging dir is
        removed — leaving no stray files (AC2).

        Args:
            archives: Ordered ZIP archive paths (e.g. from
                :func:`discover_archives`).
            handler: The parser hand-off seam, called once per extracted
                archive with an :class:`ArchiveBatch`.
            is_complete: Optional resume predicate; when it returns truthy for
                an archive, that archive is skipped. ``None`` (default) treats
                nothing as complete → full run.

        Returns:
            One :class:`ArchiveResult` per archive, in input order.

        Raises:
            Exception: Re-raises any failure from extraction or ``handler``
                (e.g. :class:`zipfile.BadZipFile`) after cleanup, so an
                interruption surfaces cleanly and the archive is left
                not-complete for the next run.
        """
        # Ensure the staging root exists up front so a misconfigured path
        # fails clearly here rather than deep inside the first extraction
        # (mirrors the early-validation discipline in import_service).
        if self._staging_root is not None:
            self._staging_root.mkdir(parents=True, exist_ok=True)

        results: list[ArchiveResult] = []
        for archive in archives:
            if is_complete is not None and is_complete(archive):
                logger.info(
                    "archive_skipped",
                    archive=str(archive),
                    reason="already complete",
                )
                results.append(ArchiveResult(archive=archive, status="skipped", txt_count=0))
                continue

            txt_count = self._extract_one(archive, handler)
            results.append(ArchiveResult(archive=archive, status="extracted", txt_count=txt_count))

        logger.info(
            "extract_archives_complete",
            total=len(results),
            extracted=sum(1 for r in results if r.status == "extracted"),
            skipped=sum(1 for r in results if r.status == "skipped"),
        )
        return results

    def _extract_one(self, archive: Path, handler: ArchiveHandler) -> int:
        """Extract one archive's ``.txt`` files, hand off, and clean up.

        The ``tempfile.TemporaryDirectory`` context manager deletes the
        staging dir on **both** the success and exception paths, so the
        extracted files never outlive this call regardless of how it exits.

        Args:
            archive: ZIP archive to extract.
            handler: Parser hand-off seam.

        Returns:
            Number of ``.txt`` files extracted and handed off.
        """
        with tempfile.TemporaryDirectory(dir=self._staging_root, prefix="firstrate_zip_") as tmp:
            staging_dir = Path(tmp)
            txt_files = self._extract_txt_members(archive, staging_dir)
            if not txt_files:
                logger.warning("archive_no_txt_members", archive=str(archive))
            batch = ArchiveBatch(
                archive=archive,
                txt_files=tuple(txt_files),
                staging_dir=staging_dir,
            )
            handler(batch)
            logger.info(
                "archive_extracted",
                archive=str(archive),
                txt_count=len(txt_files),
            )
            return len(txt_files)

    @staticmethod
    def _extract_txt_members(archive: Path, staging_dir: Path) -> list[Path]:
        """Extract only ``.txt`` members of ``archive`` into ``staging_dir``.

        Members are flattened to their basename, which neutralizes zip-slip
        path-traversal (``../`` / absolute paths collapse to a plain filename
        inside the staging dir). Backslash separators (legal in the ZIP spec
        and emitted by some Windows zippers) are normalized to ``/`` first so
        the flatten works on POSIX hosts too. Non-``.txt`` members and
        directory entries are ignored. Files are streamed to disk so large
        archives do not have to be held in memory.

        A basename collision (two members flattening to the same filename, e.g.
        nested ``a/SPY.txt`` and ``b/SPY.txt``) raises rather than silently
        overwriting — FirstRate archives are flat, so a collision signals a
        malformed archive and silent data loss must never be accepted (AC2).

        Args:
            archive: ZIP archive to read.
            staging_dir: Destination directory for extracted files.

        Returns:
            Sorted list of extracted ``.txt`` paths.

        Raises:
            zipfile.BadZipFile: If ``archive`` is not a valid ZIP.
            ValueError: If two ``.txt`` members flatten to the same basename.
        """
        extracted: list[Path] = []
        seen: set[str] = set()
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not info.filename.lower().endswith(".txt"):
                    continue
                # Normalize backslash separators before flattening to basename.
                safe_name = Path(info.filename.replace("\\", "/")).name
                if not safe_name or safe_name in {".", ".."}:
                    continue
                if safe_name in seen:
                    raise ValueError(
                        f"Duplicate .txt basename {safe_name!r} in archive {archive} — "
                        "refusing to overwrite (possible malformed archive)"
                    )
                seen.add(safe_name)
                target = staging_dir / safe_name
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(target)
        return sorted(extracted)
