"""Build and read the regime calendar: catalog bars → core labels → repository.

``build_regime_calendar`` keeps one ground-truth and one detector version per
(reference, catalog, definition). A first build creates version 1 of each; a later
build with the same definition verifies the stored bar fingerprints, appends rows for
new sessions and fills ground-truth values whose future has arrived; a changed
definition is a new version. Every refusal happens before anything is written.

``get_regime_labels`` reads stored labels by date, never computing any. Ground-truth
output carries ``hindsight: true`` and the stamp ``"hindsight, not tradeable"``.

This module is one of the named files permitted to import ``ground_truth``
(``tests/unit/governance/test_regime_ground_truth_boundary.py``).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.core.regimes.definition import (
    LABEL_SETS,
    Definition,
    GroundTruthDefinition,
    bar_hash,
    build_definition,
    canonical_json,
    definition_hash,
    validate_labels,
)
from src.core.regimes.detectors import label_detectors
from src.core.regimes.ground_truth import label_ground_truth, volatility_median
from src.core.regimes.sessions import UnsupportedSessionStamp, session_dates
from src.db.models.regime import REVISION_REASONS, RegimeCalendarVersion
from src.db.repositories.regime_repository_sync import SyncRegimeRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.bars import read_bars
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable
from src.services.provenance import REPO_ROOT, git_output

STAMP = "hindsight, not tradeable"
MAX_LABEL_ROWS = 5000
_EARLIEST = datetime(1970, 1, 1, tzinfo=timezone.utc)  # Nautilus times are unsigned ns
_LATEST = datetime(2100, 1, 1, tzinfo=timezone.utc)
_REBUILD_FIX = (
    "Label the changed bars as a new input revision: build_regime_calendar(new_revision=true, "
    "revision_reason='reference_bars_changed'). The stored version is kept as it is."
)


def load_reference_bars(catalog: str, symbol: str) -> pd.DataFrame:
    """Every daily OHLC bar of ``symbol``, indexed by its trading-session date.

    The session date comes from the bar's stamp (``src.core.regimes.sessions``): 00:00
    New York is the session date itself, 17:00 New York is the next day's session (the
    importers' session stamp; the Globex open itself is 18:00).
    A series stamped any other way is refused, never keyed by its UTC date.
    """
    frame = read_bars(catalog, symbol, "1-DAY", _EARLIEST, _LATEST)
    if frame.empty:
        raise ToolFailure(
            "no_benchmark_bars",
            f"No daily bars for {symbol} in '{catalog}'.",
            fix="Pass a benchmark_symbol with daily bars (catalog_availability).",
        )
    try:
        sessions = session_dates(pd.DatetimeIndex(frame.index))
    except UnsupportedSessionStamp as exc:
        raise ToolFailure(
            "unsupported_session_stamp",
            f"{symbol}'s daily bars in '{catalog}' have no known session date: {exc}.",
            fix="Use a catalog whose daily bars are stamped at 00:00 New York (the session "
            "date) or at 17:00 New York the day before it; other stamps are not guessed.",
        ) from None
    frame = frame[["open", "high", "low", "close"]].copy()
    frame.index = pd.Index(sessions, name="session_date")
    return frame[~frame.index.duplicated(keep="last")].sort_index()


@dataclass
class _Plan:
    """What one label set's build will write, decided before anything is written."""

    label_set: str
    definition: Definition
    existing: RegimeCalendarVersion | None
    create: RegimeCalendarVersion | None = None
    appends: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)


def _definitions(overrides: dict[str, Any] | None) -> dict[str, Definition]:
    unknown = set(overrides or {}) - set(LABEL_SETS)
    if unknown:
        raise ToolFailure(
            "invalid_definition",
            f"Unknown label set(s) in definition_overrides: {sorted(unknown)}.",
            fix="Key definition_overrides by 'ground_truth' and/or 'detector'.",
        )
    try:
        return {ls: build_definition(ls, (overrides or {}).get(ls)) for ls in LABEL_SETS}
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ToolFailure(
            "invalid_definition", f"{where}: {first['msg']}", fix="Correct the override."
        ) from None


def _revision_reason(new_revision: bool, reason: str | None, note: str | None) -> str | None:
    if not new_revision:
        if reason or note:
            raise ToolFailure(
                "invalid_revision",
                "revision_reason and revision_note are only used with new_revision=true.",
                fix="Drop them, or set new_revision=true.",
            )
        return None
    if reason not in REVISION_REASONS:
        raise ToolFailure(
            "invalid_revision",
            f"A new revision needs revision_reason, one of {', '.join(REVISION_REASONS)}.",
            fix="Say why the same definition is relabelled.",
        )
    return f"{reason}: {note.strip()}" if note and note.strip() else reason


def _labels_for(plan: _Plan, bars: pd.DataFrame, frozen: dict[str, Any]) -> list[dict]:
    if isinstance(plan.definition, GroundTruthDefinition):
        return label_ground_truth(bars, plan.definition, frozen["volatility_median"])
    return label_detectors(bars, plan.definition)


def _frozen_stats(definition: Definition, bars: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(definition, GroundTruthDefinition):
        return {}
    if len(bars) < definition.volatility_window:
        raise ToolFailure(
            "too_few_sessions",
            f"{len(bars)} daily sessions; the volatility median needs at least "
            f"{definition.volatility_window} (the centred window).",
            fix="Build again once more daily bars are loaded.",
        )
    return {
        "volatility_median": volatility_median(bars, definition),
        "sample_start": bars.index[0].isoformat(),
        "sample_end": bars.index[-1].isoformat(),
    }


def _row(day: date, labels: dict[str, Any], hash_: str, build: int) -> dict[str, Any]:
    return {
        "session_date": day,
        "labels": labels,
        "set_in_build": {k: (None if v is None else build) for k, v in labels.items()},
        "bar_hash": hash_,
        "inserted_in_build": build,
    }


def _first_difference(
    stored: dict[date, str], bars: pd.DataFrame, hashes: list[str], version: RegimeCalendarVersion
) -> dict[str, Any] | None:
    """The earliest session where the catalog no longer matches the stored range."""
    catalog = {d: h for d, h in zip(bars.index, hashes) if d <= version.data_end}
    differences = []
    for day in set(stored) | set(catalog):
        if day not in catalog:
            differences.append((day, "missing_from_catalog"))
        elif day not in stored:
            differences.append((day, "new_session_in_stored_range"))
        elif catalog[day] != stored[day]:
            differences.append((day, "bar_changed"))
    return None if not differences else dict(zip(("date", "kind"), min(differences)))


def _plan_existing(
    repo: SyncRegimeRepository, plan: _Plan, bars: pd.DataFrame, hashes: list[str]
) -> None:
    version = plan.existing
    assert version is not None
    reader = repo.ground_truth_labels if plan.label_set == "ground_truth" else repo.detector_labels
    stored = reader(version)
    fingerprints = {row.session_date: row.bar_hash for row in stored}
    diff = _first_difference(fingerprints, bars, hashes, version)
    if diff is not None:
        raise ToolFailure(
            "reference_bars_changed",
            f"{version.reference} {plan.label_set} v{version.version}: the catalog's bars no "
            f"longer match the stored range ({diff['kind']} on {diff['date']}).",
            fix=_REBUILD_FIX,
            details={**diff, "label_set": plan.label_set, "version": version.version},
        )
    computed = dict(zip(bars.index, _labels_for(plan, bars, version.frozen_stats)))
    for row in stored:
        fresh = computed[row.session_date]
        changed = [k for k, v in row.labels.items() if v is not None and fresh[k] != v]
        if changed or (plan.label_set == "detector" and fresh != row.labels):
            raise ToolFailure(
                "calendar_diverged",
                f"Recomputed {plan.label_set} labels differ from v{version.version} on "
                f"{row.session_date} ({', '.join(changed) or 'detector'}).",
                fix="The labelling code changed under a stored version: change the definition "
                "(a new version) or restore the code.",
            )
        filled = {k: fresh[k] for k, v in row.labels.items() if v is None and fresh[k] is not None}
        if filled:
            plan.fills.append({"session_date": row.session_date, "new": filled, "row": row})
    for day, hash_ in zip(bars.index, hashes):
        if day > version.data_end:
            plan.appends.append({"day": day, "labels": computed[day], "hash": hash_})


def _plan(
    repo: SyncRegimeRepository,
    key: tuple[str, str],
    label_set: str,
    definition: Definition,
    bars: pd.DataFrame,
    hashes: list[str],
    reason: str | None,
) -> _Plan:
    reference, catalog = key
    hash_ = definition_hash(definition)
    # Locked: a concurrent build of the same version waits here, then plans from the
    # committed state instead of advancing a stale build number.
    existing = repo.latest_revision(reference, catalog, label_set, hash_, for_update=True)
    plan = _Plan(label_set, definition, existing)
    # A re-cut median only concerns ground truth; detectors then build as usual.
    if reason is not None and reason.startswith("median_recut") and label_set != "ground_truth":
        reason = None
    if reason is None and existing is not None:
        _plan_existing(repo, plan, bars, hashes)
        return plan
    if reason is not None and existing is None:
        raise ToolFailure(
            "no_version_to_revise",
            f"No {label_set} version of this definition exists for {reference} in '{catalog}'.",
            fix="Build it first, without new_revision.",
        )
    revision = 1 if existing is None else existing.input_revision + 1
    plan.create = RegimeCalendarVersion(
        reference=reference,
        catalog=catalog,
        label_set=label_set,
        definition=definition.model_dump(mode="json"),
        definition_hash=hash_,
        input_revision=revision,
        revision_reason=reason,
        frozen_stats=_frozen_stats(definition, bars),
        data_start=bars.index[0],
        data_end=bars.index[-1],
    )
    labels = _labels_for(plan, bars, plan.create.frozen_stats)
    plan.appends = [{"day": d, "labels": lab, "hash": h}
                    for d, lab, h in zip(bars.index, labels, hashes)]  # fmt: skip
    return plan


def _apply(repo: SyncRegimeRepository, plan: _Plan, commit: str | None) -> dict[str, Any]:
    """Write one plan; returns its summary."""
    if plan.create is not None:
        version, action = plan.create, "created"
        repo.add_version(version, git_commit=commit)
    elif plan.appends or plan.fills:
        version = plan.existing  # type: ignore[assignment]
        action = "appended" if plan.appends else "filled"
        end = plan.appends[-1]["day"] if plan.appends else version.data_end
        repo.advance_build(version, end, git_commit=commit)
    else:
        version, action = plan.existing, "up_to_date"  # type: ignore[assignment]
    build = version.build_no
    repo.append_labels(
        version.id,
        [
            _row(a["day"], validate_labels(plan.label_set, a["labels"]), a["hash"], build)
            for a in plan.appends
        ],
    )
    repo.fill_ground_truth(version.id, [_fill(f, build) for f in plan.fills])
    return {
        **_describe(version, version.build_no, version.data_end),
        "action": action,
        "rows_added": len(plan.appends),
        "values_filled": sum(len(f["new"]) for f in plan.fills),
        "rows": repo.count_labels(version.id),
    }


def _fill(fill: dict[str, Any], build: int) -> dict[str, Any]:
    row = fill["row"]
    labels = validate_labels("ground_truth", {**row.labels, **fill["new"]})
    set_in = {**row.set_in_build, **{k: build for k in fill["new"]}}
    return {"session_date": fill["session_date"], "labels": labels, "set_in_build": set_in}


def _describe(version: RegimeCalendarVersion, build: int, data_end: date | None) -> dict:
    return {
        "label_set": version.label_set,
        "version": version.version,
        "input_revision": version.input_revision,
        "revision_reason": version.revision_reason,
        "definition": version.definition,
        "definition_hash": version.definition_hash,
        "frozen_stats": version.frozen_stats,
        "build_no": build,
        "data_start": version.data_start,
        "data_end": data_end,
        "git_commit": version.git_commit,
    }


def build_regime_calendar(
    benchmark_symbol: str = "SPY",
    catalog: str = "firstrate-etf",
    definition_overrides: dict[str, Any] | None = None,
    new_revision: bool = False,
    revision_reason: str | None = None,
    revision_note: str | None = None,
) -> dict[str, Any]:
    """Create or extend the ground-truth and detector versions for one reference."""
    reference = benchmark_symbol.strip().upper()
    definitions = _definitions(definition_overrides)
    reason = _revision_reason(new_revision, revision_reason, revision_note)
    bars = load_reference_bars(catalog, reference)
    hashes = [bar_hash(*row) for row in bars[["open", "high", "low", "close"]].itertuples(False)]
    commit = git_output(REPO_ROOT, "rev-parse", "HEAD")
    try:
        with get_sync_session() as session:
            repo = SyncRegimeRepository(session)
            plans = [
                _plan(repo, (reference, catalog), ls, definitions[ls], bars, hashes, reason)
                for ls in LABEL_SETS
            ]
            versions = {plan.label_set: _apply(repo, plan, commit) for plan in plans}
    except IntegrityError:
        raise ToolFailure(
            "concurrent_build",
            f"Another build of {reference} in '{catalog}' wrote the same version first.",
            fix="Run build_regime_calendar again; it will extend what the other build stored.",
        ) from None
    return to_jsonable(
        {
            "reference": reference,
            "catalog": catalog,
            "bars": {"start": bars.index[0], "end": bars.index[-1], "sessions": len(bars)},
            "versions": versions,
            "definitions_json": {ls: canonical_json(d) for ls, d in definitions.items()},
        }
    )


def _version_for(
    repo: SyncRegimeRepository, key: tuple[str, str, str], number: int | None
) -> RegimeCalendarVersion:
    if number is not None:
        version = repo.find_version(*key, number)
    else:  # the default definition's newest revision, never an override's version
        default = definition_hash(build_definition(key[2]))
        version = repo.latest_revision(*key, default)
    if version is None:
        what = f"version {number}" if number is not None else "default-definition calendar"
        raise ToolFailure(
            "no_calendar",
            f"No {key[2]} {what} for {key[0]} in '{key[1]}'.",
            fix="Run build_regime_calendar first (or pick a stored version).",
        )
    return version


def get_regime_labels(
    benchmark_symbol: str = "SPY",
    catalog: str = "firstrate-etf",
    label_set: str = "detector",
    start: date | None = None,
    end: date | None = None,
    version: int | None = None,
    as_of_build: int | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Stored labels for a date range, oldest first; ``next_start`` pages on."""
    if label_set not in LABEL_SETS:
        raise ToolFailure(
            "invalid_label_set", f"Unknown label_set '{label_set}'.", fix="Use 'detector' or "
            "'ground_truth'."
        )  # fmt: skip
    limit = max(1, min(limit, MAX_LABEL_ROWS))
    key = (benchmark_symbol.strip().upper(), catalog, label_set)
    with get_sync_session() as session:
        repo = SyncRegimeRepository(session)
        found = _version_for(repo, key, version)
        build = found.build_no if as_of_build is None else as_of_build
        if not 1 <= build <= found.build_no:
            raise ToolFailure(
                "unknown_build",
                f"v{found.version} has builds 1-{found.build_no}, not {build}.",
                fix="Pass an as_of_build a report recorded, or leave it unset.",
            )
        read = repo.ground_truth_labels if label_set == "ground_truth" else repo.detector_labels
        rows = read(found, start=start, end=end, as_of_build=build, limit=limit + 1)
        record = repo.find_build(found.id, build)
        described = _describe(found, build, record.data_end if record else None)
        described["build_git_commit"] = record.git_commit if record else None
    hindsight = label_set == "ground_truth"
    page = rows[:limit]
    payload: dict[str, Any] = {"hindsight": hindsight}
    if hindsight:
        payload["stamp"] = STAMP
    payload |= {
        "reference": key[0],
        "catalog": catalog,
        "calendar": described,
        "labels": [{"date": r.session_date, **r.labels} for r in page],
        "returned": len(page),
        "next_start": rows[limit].session_date if len(rows) > limit else None,
    }
    return to_jsonable(payload)
