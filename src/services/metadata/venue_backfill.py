"""Drive venue qualification across a whole ticker universe, survivably.

Resolving ~4,600 tickers against IB Gateway is a long-running job against a
service that wedges. Three properties matter more than speed:

**Resumability.** Progress is appended to a JSONL checkpoint as it happens, so a
crash, a Ctrl-C, or a wedged Gateway costs at most one flush interval. Re-running
the same command skips everything already decided.

**Stall tolerance.** IBKR error 200 ("no security definition") does not raise — the
request simply never resolves, and Nautilus's 10-second wait expires. So every
not-found ticker costs a full 10 seconds. Sequentially, a few hundred of them add
an hour of dead air. Several lanes share one token bucket: the *outbound message
rate* stays at the configured pace (which is what IBKR polices) while stalls
overlap instead of queueing.

**Not lying about throughput.** ``request_instruments(contracts=[...])`` looks like
a batch API but loops sequentially inside Nautilus, so batching buys nothing. The
only concurrency available is issuing separate requests, which is what this does.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TextIO

import structlog

from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.providers.ibkr_venue_provider import (
    VenueOutcome,
    VenueQualification,
    VenueQualifier,
)

logger = structlog.get_logger(__name__)

#: Statuses whose tickers still need a venue decision.
_WORKLIST_STATUSES: tuple[ResolutionStatus, ...] = (
    ResolutionStatus.VENUE_UNRESOLVED,
    ResolutionStatus.UNRESOLVED,
)


@dataclass(frozen=True)
class BackfillConfig:
    """Knobs for a backfill run.

    Attributes:
        state_dir: Directory holding ``checkpoint.jsonl`` and the report CSVs.
        rate_per_s: Outbound request rate. 6/s is ~12% of IBKR's message budget and
            still clears 4,600 tickers in ~13 minutes of issue time; the endpoint is
            pacing-sensitive and there is no throughput reason to push harder.
        concurrency: In-flight requests. The load-bearing knob — see module docstring.
        flush_every: Checkpoint flush cadence, in records (~8s of work at 6/s).
        limit: Stop after this many *new* attempts (smoke tests).
        retry_errors: Re-attempt tickers checkpointed as ERROR.
        max_reconnects: Give up after this many Gateway reconnects.
        include_resolved: Also re-verify tickers already RESOLVED.
        backoff_base_s: First reconnect backoff; each subsequent one triples it up
            to 90s. Injectable so tests can exercise the reconnect path without
            actually sleeping through it.
    """

    state_dir: Path
    rate_per_s: float = 6.0
    concurrency: int = 4
    flush_every: int = 50
    limit: Optional[int] = None
    retry_errors: bool = True
    max_reconnects: int = 10
    include_resolved: bool = False
    backoff_base_s: float = 5.0

    @property
    def checkpoint_path(self) -> Path:
        return self.state_dir / "checkpoint.jsonl"


@dataclass
class BackfillTally:
    """Running counts for one backfill run."""

    resolved: int = 0
    not_found: int = 0
    ambiguous: int = 0
    error: int = 0
    skipped: int = 0
    reconnects: int = 0
    stopped_early: bool = False

    @property
    def attempted(self) -> int:
        """Tickers this run actually asked IBKR about."""
        return self.resolved + self.not_found + self.ambiguous + self.error

    def record(self, outcome: VenueOutcome) -> None:
        setattr(self, outcome.value, getattr(self, outcome.value) + 1)

    def as_result_line(self, elapsed_s: float) -> str:
        """The single machine-parseable line every run ends with."""
        status = "partial" if self.stopped_early else "ok"
        return (
            f"RESULT: {status} resolved={self.resolved} not_found={self.not_found} "
            f"ambiguous={self.ambiguous} error={self.error} skipped={self.skipped} "
            f"reconnects={self.reconnects} elapsed={elapsed_s:.1f}"
        )


class RateLimiter:
    """Token bucket shared by all lanes.

    Separate from ``ibkr_client.RateLimiter``, which is hardcoded to the 45/s *bar*
    budget and takes an int. Contract-details pacing is a different, slower budget
    and needs a fractional rate.
    """

    def __init__(self, rate_per_s: float, *, clock: Callable[[], float] = time.monotonic):
        self._interval = 1.0 / rate_per_s if rate_per_s > 0 else 0.0
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        """Block until this caller's turn in the outbound schedule."""
        async with self._lock:
            now = self._clock()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)


def load_worklist(meta_repo, *, include_resolved: bool = False) -> list[str]:
    """Tickers still needing a venue decision, sorted for deterministic resume.

    Args:
        meta_repo: Sync metadata repository (``list_by_status``).
        include_resolved: Also return RESOLVED tickers, to re-verify FMP's answers
            against IBKR. EXCLUDED tickers are never included — they were
            adjudicated by a human and re-asking a machine does not overturn that.

    Returns:
        Sorted, de-duplicated ticker list.
    """
    statuses = list(_WORKLIST_STATUSES)
    if include_resolved:
        statuses.append(ResolutionStatus.RESOLVED)
    tickers = {row.ticker for status in statuses for row in meta_repo.list_by_status(status)}
    return sorted(tickers)


def load_checkpoint(path: Path) -> dict[str, VenueQualification]:
    """Rebuild prior results from the append-only JSONL checkpoint.

    A torn final line — the normal shape of a hard crash mid-write — is dropped
    rather than raising. That is what makes the format crash-safe by construction:
    there is no separate commit step that can be missed.

    Args:
        path: Checkpoint file (absent is fine — a fresh run).

    Returns:
        ``{ticker: VenueQualification}``, last write wins per ticker.
    """
    if not path.exists():
        return {}
    results: dict[str, VenueQualification] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                results[raw["ticker"]] = VenueQualification(
                    ticker=raw["ticker"],
                    outcome=VenueOutcome(raw["outcome"]),
                    venue=raw.get("venue"),
                    primary_exchange=raw.get("primary_exchange"),
                    con_id=raw.get("con_id"),
                    candidates=tuple(raw.get("candidates") or ()),
                    detail=raw.get("detail", ""),
                    attempts=raw.get("attempts", 1),
                    elapsed_s=raw.get("elapsed_s", 0.0),
                    unexpected_venue=raw.get("unexpected_venue", False),
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                logger.warning("checkpoint_line_dropped", path=str(path), line=line[:120])
    return results


def to_record(qual: VenueQualification, *, at: Optional[datetime] = None) -> dict:
    """Serialise one qualification for the checkpoint."""
    stamp = at or datetime.now(timezone.utc)
    return {
        "ticker": qual.ticker,
        "outcome": qual.outcome.value,
        "venue": qual.venue,
        "primary_exchange": qual.primary_exchange,
        "con_id": qual.con_id,
        "candidates": list(qual.candidates),
        "detail": qual.detail,
        "attempts": qual.attempts,
        "elapsed_s": round(qual.elapsed_s, 3),
        "unexpected_venue": qual.unexpected_venue,
        "ts": stamp.isoformat(),
    }


class CheckpointWriter:
    """Append-only JSONL writer that flushes on a cadence."""

    def __init__(self, path: Path, *, flush_every: int = 50):
        self._path = path
        self._flush_every = max(1, flush_every)
        self._since_flush = 0
        self._handle: Optional[TextIO] = None

    def __enter__(self) -> "CheckpointWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None

    def write(self, qual: VenueQualification) -> None:
        assert self._handle is not None, "CheckpointWriter used outside its context"
        self._handle.write(json.dumps(to_record(qual)) + "\n")
        self._since_flush += 1
        if self._since_flush >= self._flush_every:
            self._handle.flush()
            self._since_flush = 0


def select_pending(
    tickers: Iterable[str],
    done: dict[str, VenueQualification],
    *,
    retry_errors: bool = True,
    limit: Optional[int] = None,
) -> list[str]:
    """Filter a worklist down to what still needs asking.

    Terminal outcomes (resolved / not_found / ambiguous) are never re-asked: IBKR
    gave an answer, and a second identical question costs 10 seconds for
    not-founds. ERROR means the request never resolved, so it is retried by default.
    """
    pending = []
    for ticker in tickers:
        prior = done.get(ticker)
        if prior is not None and (prior.is_terminal or not retry_errors):
            continue
        pending.append(ticker)
        if limit is not None and len(pending) >= limit:
            break
    return pending


async def run_backfill(
    *,
    tickers: list[str],
    qualifier: VenueQualifier,
    config: BackfillConfig,
    writer: CheckpointWriter,
    reconnect: Optional[Callable[[], Awaitable[None]]] = None,
    stop_flag: Optional[asyncio.Event] = None,
    already_done: int = 0,
    on_progress: Optional[Callable[[int, int, BackfillTally], None]] = None,
) -> tuple[BackfillTally, dict[str, VenueQualification]]:
    """Qualify every pending ticker, tolerating stalls and Gateway wedges.

    Args:
        tickers: Pending tickers (already filtered by ``select_pending``).
        qualifier: Per-ticker venue resolver.
        config: Rate, concurrency, flush cadence, reconnect budget.
        writer: Open checkpoint writer.
        reconnect: Called after a run of consecutive failures; rebuilds the client.
        stop_flag: Set by the signal handler to drain and exit cleanly.
        already_done: Count of previously-checkpointed tickers, for the tally.
        on_progress: Called after each completion with ``(done, total, tally)``.

    Returns:
        ``(tally, results)`` — results keyed by ticker for this run only.
    """
    tally = BackfillTally(skipped=already_done)
    results: dict[str, VenueQualification] = {}
    limiter = RateLimiter(config.rate_per_s)
    lock = asyncio.Lock()
    state = {"consecutive_errors": 0, "completed": 0}
    total = len(tickers)

    # A queue with independent consumers, NOT chunked gather. Chunking would put a
    # barrier at every chunk boundary, so one 10-second error-200 stall would idle
    # its lane-mates until it finished — reintroducing exactly the dead air the
    # concurrency exists to hide.
    queue: asyncio.Queue = asyncio.Queue()
    for ticker in tickers:
        queue.put_nowait(ticker)

    async def handle_failure_streak() -> None:
        """Reconnect once a run of failures suggests the Gateway, not the ticker."""
        if reconnect is None or tally.reconnects >= config.max_reconnects:
            return
        backoff = min(90.0, config.backoff_base_s * (3 ** min(tally.reconnects, 3)))
        logger.warning(
            "venue_backfill_reconnecting",
            consecutive_errors=state["consecutive_errors"],
            reconnect_number=tally.reconnects + 1,
            backoff_s=backoff,
        )
        await asyncio.sleep(backoff)
        await reconnect()
        tally.reconnects += 1
        state["consecutive_errors"] = 0

    async def lane() -> None:
        while True:
            if stop_flag is not None and stop_flag.is_set():
                return
            try:
                ticker = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            await limiter.acquire()
            qual = await qualifier.qualify(ticker)

            async with lock:
                results[ticker] = qual
                tally.record(qual.outcome)
                writer.write(qual)
                state["completed"] += 1
                state["consecutive_errors"] = (
                    state["consecutive_errors"] + 1 if qual.outcome is VenueOutcome.ERROR else 0
                )
                if on_progress is not None:
                    on_progress(state["completed"], total, tally)
                needs_reconnect = state["consecutive_errors"] >= 5

            if needs_reconnect:
                await handle_failure_streak()

    await asyncio.gather(*(lane() for _ in range(max(1, config.concurrency))))

    if stop_flag is not None and stop_flag.is_set():
        tally.stopped_early = True
    return tally, results
