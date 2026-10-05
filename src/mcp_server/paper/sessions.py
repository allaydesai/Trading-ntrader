"""``list_sessions`` and ``get_session``: paper sessions beside their expectation band (S7.2, S7.3).

Read-only: every query runs in a ``READ ONLY`` transaction. The server never
starts, stops, creates or seals a session.
"""

from datetime import datetime, timezone
from typing import Any

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.paper import reads
from src.mcp_server.paper.drift import health_flags
from src.mcp_server.paper.report import check_horizon, horizon, session_health, session_report
from src.mcp_server.paper.views import summary_view
from src.mcp_server.settings import McpSettings
from src.mcp_server.studies.gates import load_gates
from src.models.session import SessionStatus

MAX_RECENT = 200


def g4_thresholds(settings: McpSettings) -> dict[str, Any]:
    """The vault's G4 thresholds, or nothing when the gates block has none."""
    thresholds = load_gates(settings).thresholds or {}
    return dict(thresholds.get("G4") or {})


def _status(status: str | None) -> str | None:
    if status is None:
        return None
    try:
        return SessionStatus(status.strip().lower()).value
    except ValueError:
        options = ", ".join(s.value for s in SessionStatus)
        raise ToolFailure(
            "invalid_status", f"No status {status!r}.", fix=f"Use {options}."
        ) from None


def _tests(link: reads.Link | None, study: str) -> bool:
    return link is not None and study in (link.study.slug, str(link.study.study_id))


def list_sessions(
    status: str | None = None,
    study: str | None = None,
    limit: int = 50,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Paper sessions, newest first, with their progress and the study they test."""
    if limit < 1:
        raise ToolFailure("invalid_request", "limit must be at least 1.", fix="Omit it.")
    now = now or datetime.now(timezone.utc)
    with reads.read_only_session() as db:
        rows = reads.sessions(db, status=_status(status))
        counts = reads.closed_counts(db, [r.id for r in rows])
        found = []
        for row in rows:
            link = reads.link_of(db, row.linked_backtest_run_id)
            if study is not None and not _tests(link, study):
                continue
            weeks = horizon(row, now, None)["weeks"]
            alive = None
            if str(row.status) == "running":
                flags = health_flags(session_health(row), now)
                alive = not any(f["kind"] == "not_alive" for f in flags)
            found.append(summary_view(row, link, counts.get(row.id, 0), weeks, alive))
    return to_jsonable({"sessions": found[:limit], "total": len(found)})


def get_session(
    settings: McpSettings,
    key: str,
    *,
    weeks: float | None = None,
    recent: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One session read against its band, with drift flags and G4 progress."""
    check_horizon(weeks=weeks, trades=None)
    g4 = g4_thresholds(settings)
    with reads.read_only_session() as db:
        row = reads.resolve_session(db, key)
        report = session_report(
            db,
            row,
            g4=g4,
            now=now or datetime.now(timezone.utc),
            weeks=weeks,
            recent=max(0, min(recent, MAX_RECENT)),
        )
    return to_jsonable(report)
