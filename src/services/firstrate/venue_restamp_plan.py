"""Plan the parquet moves needed when a ticker's venue is corrected.

Bars are written to ``data/bar/{TICKER}.{VENUE}-{SPEC}-EXTERNAL/`` and the venue is
also stamped into each file's parquet schema metadata (``bar_type`` and
``instrument_id``). So a corrected venue does not merely relabel a row — it changes
where the bars must live and what they must claim to be. Leave either behind and
the bars are orphaned: reachable on disk, unreachable by identity.

That is the state the ETF catalog is in. Every ticker was imported under the
provisional exchange from FirstRate's ``company_profiles.csv``, and IBKR
qualification is now correcting many of them.

This module is the pure half: read the directory tree, read the target venues, and
decide what moves. It performs no I/O beyond listing and stat-ing, so the plan can
be printed and argued with before a single byte is rewritten.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

#: ``{INSTRUMENT_ID}-{SPEC}-EXTERNAL`` where SPEC is e.g. ``1-DAY-LAST``.
#: The instrument-id group is non-greedy so the spec (which has a fixed shape)
#: anchors the split — an instrument id may itself contain hyphens.
_DIR_RE = re.compile(r"^(?P<iid>.+?)-(?P<spec>\d+-(?:DAY|HOUR|MINUTE)-[A-Z]+)-EXTERNAL$")


@dataclass(frozen=True)
class RestampAction:
    """One directory's move, fully determined before anything is touched."""

    ticker: str
    old_venue: str
    new_venue: str
    spec: str
    src_dir: Path
    dst_dir: Path
    files: tuple[Path, ...]
    total_bytes: int

    @property
    def old_instrument_id(self) -> str:
        return f"{self.ticker}.{self.old_venue}"

    @property
    def new_instrument_id(self) -> str:
        return f"{self.ticker}.{self.new_venue}"

    @property
    def old_bar_type(self) -> str:
        return f"{self.old_instrument_id}-{self.spec}-EXTERNAL"

    @property
    def new_bar_type(self) -> str:
        return f"{self.new_instrument_id}-{self.spec}-EXTERNAL"


@dataclass(frozen=True)
class RestampPlan:
    """Everything the executor needs, plus everything the operator should see first."""

    catalog: str
    catalog_root: Path
    actions: tuple[RestampAction, ...] = ()
    no_ops: int = 0
    excluded: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()
    collisions: tuple[str, ...] = ()
    unsafe: tuple[str, ...] = ()
    unparsed: tuple[str, ...] = ()

    @property
    def bytes_to_rewrite(self) -> int:
        return sum(a.total_bytes for a in self.actions)

    @property
    def tickers_affected(self) -> int:
        return len({a.ticker for a in self.actions})

    @property
    def is_safe(self) -> bool:
        """No unexplained directory anywhere in the tree.

        Collisions, unreadable directories, and unrecognised names each mean the
        tree is not in the shape the plan assumes. Rewriting 39 GB on that basis is
        not a risk worth taking for the minutes it saves.
        """
        return not (self.collisions or self.unsafe or self.orphans or self.unparsed)

    def peak_extra_bytes(self, workers: int) -> int:
        """Transient disk needed: each worker holds one in-flight copy."""
        if not self.actions:
            return 0
        largest = sorted((a.total_bytes for a in self.actions), reverse=True)
        return sum(largest[: max(1, workers)])

    def moves_by_venue_pair(self) -> dict[tuple[str, str], tuple[int, int]]:
        """``{(old, new): (dir_count, bytes)}`` — the operator-facing summary."""
        summary: dict[tuple[str, str], tuple[int, int]] = {}
        for action in self.actions:
            key = (action.old_venue, action.new_venue)
            count, size = summary.get(key, (0, 0))
            summary[key] = (count + 1, size + action.total_bytes)
        return summary


def parse_bar_dir(name: str) -> Optional[tuple[str, str, str]]:
    """Split a bar directory name into ``(ticker, venue, spec)``.

    Uses ``rsplit('.', 1)`` on the instrument id: a ticker may itself contain dots
    (``BRK.B.NYSE``), and splitting from the left would silently produce ticker
    ``BRK`` with venue ``B``. ``backtest_loader.build_equity`` documents the same
    hazard. No such ticker exists in the ETF catalog today, but this tool will be
    pointed at the stock catalog, which has them.

    Returns:
        ``(ticker, venue, spec)``, or ``None`` if the name is not a bar directory.
    """
    match = _DIR_RE.match(name)
    if match is None:
        return None
    instrument_id = match.group("iid")
    if "." not in instrument_id:
        return None
    ticker, venue = instrument_id.rsplit(".", 1)
    if not ticker or not venue:
        return None
    return ticker, venue, match.group("spec")


def _directory_files(directory: Path) -> Optional[tuple[tuple[Path, ...], int]]:
    """Return ``(parquet_files, total_bytes)``, or ``None`` if anything else is present.

    A stray file means the directory is not what this tool thinks it is; refusing to
    plan it is cheaper than discovering the surprise mid-rewrite.
    """
    files: list[Path] = []
    total = 0
    for entry in sorted(directory.iterdir()):
        if entry.is_dir() or entry.suffix != ".parquet":
            return None
        files.append(entry)
        total += entry.stat().st_size
    if not files:
        return None
    return tuple(files), total


def build_restamp_plan(
    *,
    catalog: str,
    catalog_root: Path,
    target_venues: Mapping[str, str],
    excluded_tickers: frozenset[str] = frozenset(),
) -> RestampPlan:
    """Decide what must move, without moving anything.

    Args:
        catalog: Catalog name (reporting only).
        catalog_root: Catalog directory containing ``data/bar/``.
        target_venues: ``{ticker: venue}`` for every ticker that *should* have a
            qualified identity — read from ``catalog_instruments`` after the venue
            decisions have been applied, so the DB is the authority here.
        excluded_tickers: Tickers deliberately unqualified via the exclusion
            register. They keep their bars where they are; classified separately so
            they are not mistaken for orphans.

    Returns:
        A ``RestampPlan``. Callers must check ``is_safe`` before executing.
    """
    bar_root = catalog_root / "data" / "bar"
    if not bar_root.is_dir():
        return RestampPlan(catalog=catalog, catalog_root=catalog_root)

    actions: list[RestampAction] = []
    excluded: list[str] = []
    orphans: list[str] = []
    collisions: list[str] = []
    unsafe: list[str] = []
    unparsed: list[str] = []
    no_ops = 0
    existing_dirs = {entry.name for entry in bar_root.iterdir() if entry.is_dir()}

    for name in sorted(existing_dirs):
        parsed = parse_bar_dir(name)
        if parsed is None:
            unparsed.append(name)
            continue
        ticker, old_venue, spec = parsed

        target = target_venues.get(ticker)
        if target is None:
            # No qualified identity for this ticker. Excluded is a decision; anything
            # else is a surprise worth stopping for.
            (excluded if ticker in excluded_tickers else orphans).append(name)
            continue
        if target == old_venue:
            no_ops += 1
            continue

        dst_name = f"{ticker}.{target}-{spec}-EXTERNAL"
        if dst_name in existing_dirs:
            collisions.append(f"{name} -> {dst_name}")
            continue

        contents = _directory_files(bar_root / name)
        if contents is None:
            unsafe.append(name)
            continue
        files, total_bytes = contents

        actions.append(
            RestampAction(
                ticker=ticker,
                old_venue=old_venue,
                new_venue=target,
                spec=spec,
                src_dir=bar_root / name,
                dst_dir=bar_root / dst_name,
                files=files,
                total_bytes=total_bytes,
            )
        )

    # Largest first: the tail of a parallel run is otherwise one huge file finishing
    # alone while every other worker idles.
    actions.sort(key=lambda a: (-a.total_bytes, a.src_dir.name))

    plan = RestampPlan(
        catalog=catalog,
        catalog_root=catalog_root,
        actions=tuple(actions),
        no_ops=no_ops,
        excluded=tuple(excluded),
        orphans=tuple(orphans),
        collisions=tuple(collisions),
        unsafe=tuple(unsafe),
        unparsed=tuple(unparsed),
    )
    logger.info(
        "restamp_plan_built",
        catalog=catalog,
        actions=len(plan.actions),
        no_ops=plan.no_ops,
        excluded=len(plan.excluded),
        orphans=len(plan.orphans),
        collisions=len(plan.collisions),
        unsafe=len(plan.unsafe),
    )
    return plan
