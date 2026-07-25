"""Execute a venue re-stamp: rewrite parquet metadata and move the partition.

The venue lives in two places per partition — the directory name and each file's
parquet schema metadata (``bar_type`` / ``instrument_id``). ``ParquetDataCatalog``
finds files by directory name and then decodes them via
``BarDataWranglerV2.from_schema``, which reads the venue back out of the metadata.
Change one without the other and the partition either cannot be found or decodes
as an instrument it is not.

The OHLCV columns are raw ``fixed_size_binary[16]`` Nautilus encodings, so the
venue appears nowhere in the row data. That is what makes this cheap and, more
importantly, lossless: bars are copied verbatim rather than re-derived from source
CSVs. Re-importing would re-run a float→raw conversion and risk producing different
bars than the ones the explorer-accuracy and reference-consistency stories already
signed off on.

Deliberately imports no ``nautilus_trader`` at module scope: the rewrite runs in a
process pool, and initialising Nautilus logging in a worker risks the LogGuard
double-init panic (CLAUDE.md gotcha #1). The one verification that does need
Nautilus runs in the parent and imports it locally.
"""

import hashlib
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import pyarrow.parquet as pq  # type: ignore[import-untyped]  # pyarrow ships no stubs
import structlog

from src.services.firstrate.venue_restamp_plan import RestampAction, RestampPlan

logger = structlog.get_logger(__name__)

#: Matches ParquetDataCatalog.max_rows_per_group, and the row-group size observed
#: in the live catalog. Keeping it identical means the rewritten file has the same
#: row-group boundaries as the original, which is what makes the per-row-group
#: statistics comparison in V2 a meaningful check rather than a coincidence.
_ROW_GROUP_SIZE = 5000

#: Read back from the live catalog's own footers, not assumed.
_COMPRESSION = "snappy"
_FORMAT_VERSION = "2.6"

#: Footer keys carrying the instrument identity; everything else is copied verbatim.
_BAR_TYPE_KEY = b"bar_type"
_INSTRUMENT_ID_KEY = b"instrument_id"


class ActionState(str, Enum):
    """What the filesystem says about an action, independent of any checkpoint."""

    PENDING = "pending"  # src only — not started
    INTERRUPTED = "interrupted"  # both — a previous run died mid-copy
    DONE = "done"  # dst only — finished
    MISSING = "missing"  # neither — unexplained; abort


@dataclass
class FileResult:
    """Per-file outcome of a rewrite."""

    src: Path
    dst: Path
    rows_in: int
    rows_out: int
    ok: bool
    detail: str = ""


@dataclass
class RestampOutcome:
    """Aggregate outcome of an execution run."""

    completed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    bytes_written: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def classify_action_state(action: RestampAction) -> ActionState:
    """Derive resume state from the filesystem alone.

    The checkpoint log is an audit trail, not a correctness requirement: the four
    combinations of (src exists, dst exists) fully determine what to do, so a lost
    or stale log can never cause double-work or data loss.
    """
    src, dst = action.src_dir.exists(), action.dst_dir.exists()
    if src and not dst:
        return ActionState.PENDING
    if src and dst:
        return ActionState.INTERRUPTED
    if dst and not src:
        return ActionState.DONE
    return ActionState.MISSING


def restamp_file(src: Path, dst: Path, *, new_bar_type: str, new_instrument_id: str) -> FileResult:
    """Stream-rewrite one parquet file with a corrected identity in its footer.

    Streams by row group rather than reading the table whole: bounded memory
    regardless of file size. Compression, format version and row-group size are
    pinned to what the live catalog already uses, so the output is byte-comparable
    in structure and Nautilus reads it exactly as before.

    ``price_precision`` / ``size_precision`` are copied untouched — they are
    per-instrument facts that a venue correction does not change, and rewriting them
    would silently alter how prices decode.
    """
    reader = pq.ParquetFile(src)
    metadata = dict(reader.schema_arrow.metadata or {})
    metadata[_BAR_TYPE_KEY] = new_bar_type.encode()
    metadata[_INSTRUMENT_ID_KEY] = new_instrument_id.encode()
    schema = reader.schema_arrow.with_metadata(metadata)

    rows_in = reader.metadata.num_rows
    rows_out = 0
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    try:
        with pq.ParquetWriter(
            tmp, schema, compression=_COMPRESSION, version=_FORMAT_VERSION
        ) as writer:
            for batch in reader.iter_batches(batch_size=_ROW_GROUP_SIZE):
                writer.write_batch(batch)
                rows_out += batch.num_rows
        # Atomic within the directory: a crash must never leave a half-written file
        # under the real name, where a later run would mistake it for finished work.
        os.replace(tmp, dst)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return FileResult(
            src, dst, rows_in, rows_out, ok=False, detail=f"{type(exc).__name__}: {exc}"
        )

    return FileResult(src, dst, rows_in, rows_out, ok=True)


def verify_file(
    src: Path, dst: Path, *, new_bar_type: str, new_instrument_id: str
) -> Optional[str]:
    """V1 — structural check on one rewritten file. Returns an error, or ``None``.

    Confirms the identity actually changed, that nothing else in the footer did,
    that the column schema is untouched, and that no rows were lost.
    """
    src_file, dst_file = pq.ParquetFile(src), pq.ParquetFile(dst)
    src_meta = dict(src_file.schema_arrow.metadata or {})
    dst_meta = dict(dst_file.schema_arrow.metadata or {})

    if dst_meta.get(_BAR_TYPE_KEY) != new_bar_type.encode():
        return f"bar_type not restamped: {dst_meta.get(_BAR_TYPE_KEY)!r}"
    if dst_meta.get(_INSTRUMENT_ID_KEY) != new_instrument_id.encode():
        return f"instrument_id not restamped: {dst_meta.get(_INSTRUMENT_ID_KEY)!r}"

    for key in (b"price_precision", b"size_precision"):
        if src_meta.get(key) != dst_meta.get(key):
            return f"{key.decode()} changed: {src_meta.get(key)!r} -> {dst_meta.get(key)!r}"

    if not dst_file.schema_arrow.remove_metadata().equals(src_file.schema_arrow.remove_metadata()):
        return "column schema changed"
    if dst_file.metadata.num_rows != src_file.metadata.num_rows:
        return f"row count changed: {src_file.metadata.num_rows} -> {dst_file.metadata.num_rows}"
    return None


def verify_row_groups(src: Path, dst: Path, *, deep: bool = False) -> Optional[str]:
    """V2 — per-row-group content check. Returns an error, or ``None``.

    Compares every row group's min/max statistics for every column. Those live in
    the footer already, so this verifies row alignment and every row-group boundary
    at essentially zero I/O cost — far stronger than a row count, which a shifted
    boundary would sail straight through.

    ``deep`` additionally byte-hashes the first and last row groups, catching
    corruption that preserves statistics.
    """
    src_file, dst_file = pq.ParquetFile(src), pq.ParquetFile(dst)
    src_md, dst_md = src_file.metadata, dst_file.metadata

    if src_md.num_row_groups != dst_md.num_row_groups:
        return f"row-group count changed: {src_md.num_row_groups} -> {dst_md.num_row_groups}"

    for rg in range(src_md.num_row_groups):
        src_rg, dst_rg = src_md.row_group(rg), dst_md.row_group(rg)
        if src_rg.num_rows != dst_rg.num_rows:
            return f"row group {rg} size changed: {src_rg.num_rows} -> {dst_rg.num_rows}"
        for col in range(src_md.num_columns):
            src_stats = src_rg.column(col).statistics
            dst_stats = dst_rg.column(col).statistics
            if src_stats is None or dst_stats is None:
                continue
            if (src_stats.min, src_stats.max) != (dst_stats.min, dst_stats.max):
                name = src_md.schema.column(col).name
                return f"row group {rg} column {name} statistics changed"

    if deep and src_md.num_row_groups:
        for rg in {0, src_md.num_row_groups - 1}:
            if _row_group_digest(src_file, rg) != _row_group_digest(dst_file, rg):
                return f"row group {rg} content differs"
    return None


def _row_group_digest(handle: pq.ParquetFile, index: int) -> str:
    """Stable hash of one row group's values, ignoring metadata."""
    table = handle.read_row_group(index).replace_schema_metadata(None)
    digest = hashlib.sha256()
    for column in table.columns:
        for chunk in column.chunks:
            for buffer in chunk.buffers():
                if buffer is not None:
                    digest.update(buffer)
    return digest.hexdigest()


def execute_action(action: RestampAction, *, deep_verify: bool = False) -> tuple[bool, str]:
    """Rewrite one partition: copy to the new directory, verify, then drop the old.

    Write-new-then-delete-old, never rewrite-in-place. An interrupted in-place
    rewrite destroys the only copy of the bars; this way the original survives until
    every file has been written *and* verified.
    """
    state = classify_action_state(action)
    if state is ActionState.DONE:
        return True, "already done"
    if state is ActionState.MISSING:
        return False, "neither source nor destination exists"
    if state is ActionState.INTERRUPTED:
        # A previous run died mid-copy. The partial destination is untrustworthy and
        # the source is intact, so discard and redo.
        shutil.rmtree(action.dst_dir)

    action.dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        for src in action.files:
            dst = action.dst_dir / src.name
            result = restamp_file(
                src,
                dst,
                new_bar_type=action.new_bar_type,
                new_instrument_id=action.new_instrument_id,
            )
            if not result.ok:
                return False, f"{src.name}: {result.detail}"

            error = verify_file(
                src,
                dst,
                new_bar_type=action.new_bar_type,
                new_instrument_id=action.new_instrument_id,
            )
            if error is None:
                error = verify_row_groups(src, dst, deep=deep_verify)
            if error is not None:
                return False, f"{src.name}: {error}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    # Everything verified — only now is the original expendable.
    shutil.rmtree(action.src_dir)
    return True, "ok"


def _execute_action_entry(payload: dict) -> tuple[str, bool, str]:
    """Process-pool entry point.

    Takes plain data rather than a RestampAction so the payload pickles cheaply and
    the worker never imports anything beyond pyarrow.
    """
    action = RestampAction(
        ticker=payload["ticker"],
        old_venue=payload["old_venue"],
        new_venue=payload["new_venue"],
        spec=payload["spec"],
        src_dir=Path(payload["src_dir"]),
        dst_dir=Path(payload["dst_dir"]),
        files=tuple(Path(f) for f in payload["files"]),
        total_bytes=payload["total_bytes"],
    )
    ok, detail = execute_action(action, deep_verify=payload["deep_verify"])
    return action.src_dir.name, ok, detail


def _to_payload(action: RestampAction, *, deep_verify: bool) -> dict:
    return {
        "ticker": action.ticker,
        "old_venue": action.old_venue,
        "new_venue": action.new_venue,
        "spec": action.spec,
        "src_dir": str(action.src_dir),
        "dst_dir": str(action.dst_dir),
        "files": [str(f) for f in action.files],
        "total_bytes": action.total_bytes,
        "deep_verify": deep_verify,
    }


def execute_plan(
    plan: RestampPlan,
    *,
    workers: int = 6,
    deep_verify: bool = False,
    journal_path: Optional[Path] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> RestampOutcome:
    """Run every action in the plan, in parallel, stopping the run on any failure.

    Processes rather than threads: snappy encoding is CPU-bound, and a pyarrow
    segfault takes down one worker instead of the whole run.
    """
    outcome = RestampOutcome()
    if not plan.actions:
        return outcome

    journal = None
    if journal_path is not None:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal = journal_path.open("a", encoding="utf-8")

    try:
        with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(_execute_action_entry, _to_payload(a, deep_verify=deep_verify)): a
                for a in plan.actions
            }
            for index, future in enumerate(as_completed(futures), start=1):
                action = futures[future]
                name, ok, detail = future.result()
                if ok:
                    if detail == "already done":
                        outcome.skipped += 1
                    else:
                        outcome.completed += 1
                        outcome.bytes_written += action.total_bytes
                else:
                    outcome.failed.append(f"{name}: {detail}")
                    logger.error("restamp_action_failed", directory=name, detail=detail)
                if journal is not None:
                    journal.write(
                        json.dumps(
                            {
                                "directory": name,
                                "new_bar_type": action.new_bar_type,
                                "ok": ok,
                                "detail": detail,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        + "\n"
                    )
                    journal.flush()
                if on_progress is not None:
                    on_progress(index, len(plan.actions), name)
    finally:
        if journal is not None:
            journal.close()

    return outcome
