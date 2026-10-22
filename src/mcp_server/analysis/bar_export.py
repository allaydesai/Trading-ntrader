"""``export_bars``: a study's in-sample bars to a CSV in the vault, for concept probes (S2.5).

The window is always clamped to the in-sample window, and the result says what
was cut: out-of-sample bars never leave the catalog this way. Each export is
recorded on the study.
"""

import csv
import os
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.bars import COLUMNS, read_bars
from src.mcp_server.errors import ToolFailure
from src.mcp_server.export import refuse_existing, vault_directory
from src.mcp_server.settings import McpSettings
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.split import Split, clamp_to_in_sample


def _utc(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=timezone.utc)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    """Write the bars atomically: a reader never sees a half-written file."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with tmp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time", *COLUMNS])
            for stamp, row in zip(frame.index, frame.itertuples(index=False)):
                writer.writerow([stamp.isoformat(), *row])
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def export_bars(
    settings: McpSettings,
    study_key: str,
    *,
    slug: str,
    folder: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    start: date | None = None,
    end: date | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write ``<vault>/<folder>/<slug>.bars.csv`` with the study's in-sample bars."""
    with get_sync_session() as session:
        study = load_study(SyncResearchRepository(session), study_key)
        symbol = (symbol or study.symbols[0]).strip().upper()
        timeframe = timeframe or study.timeframe
        catalog, slug_of_study = study.catalog, study.slug
        split = Split.of(study)
        requested = {"start": str(start or split.is_start), "end": str(end or split.is_end)}
        start, end, notes = clamp_to_in_sample(split, start, end)
    path = vault_directory(settings, folder, slug) / f"{slug}.bars.csv"
    refuse_existing([path], slug=slug, folder=folder, overwrite=overwrite)
    frame = read_bars(catalog, symbol, timeframe, _utc(start, time.min), _utc(end, time.max))
    if frame.empty:
        raise ToolFailure(
            "data_not_found",
            f"No {timeframe} bars for {symbol} in '{catalog}' from {start} to {end}.",
            fix="Check catalog_availability for this symbol and timeframe.",
        )
    if len(frame) > settings.max_bar_rows:
        raise ToolFailure(
            "too_many_bars",
            f"{len(frame)} bars exceed the export limit of {settings.max_bar_rows}.",
            fix="Shorten the window or use a coarser timeframe.",
        )
    _write_csv(path, frame)
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        details = {
            "file": str(path),
            "symbol": symbol,
            "start": str(start),
            "end": str(end),
            "requested": requested,
            "clamped": notes,
        }
        repo.add_event(load_study(repo, slug_of_study), "bars_exported", None, details)
    return {
        "file": str(path),
        "study": slug_of_study,
        "symbol": symbol,
        "timeframe": timeframe,
        "catalog": catalog,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": len(frame),
        "first_bar": frame.index[0].isoformat(),
        "last_bar": frame.index[-1].isoformat(),
        "clamped": notes,
    }
