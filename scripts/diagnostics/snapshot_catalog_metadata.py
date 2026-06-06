"""Catalog metadata snapshot tool (Story 3-6).

Reads ``catalog_instruments`` rows for one or more named catalogs and writes
a JSON snapshot keyed by ``(catalog, ticker, timeframe)``. Used as a pre- and
post-import bookmark when re-importing FirstRate data through the timezone-
corrected parser — diffing the two snapshots verifies bar counts moved <0.5%
and date ranges shifted in the expected direction.

The snapshot is read-only. It never mutates the catalog or the database.

Usage::

    uv run python scripts/diagnostics/snapshot_catalog_metadata.py \\
        --catalog e2e-test \\
        --output /tmp/story-3-6-evidence/pre-import-metadata.json

Multiple ``--catalog`` flags snapshot more than one catalog in a single run.
Omit ``--catalog`` to snapshot every catalog discovered under
``CATALOG_BASE_PATH``.

Output schema::

    {
        "captured_at": "<ISO-8601 UTC>",
        "catalogs": [
            {
                "catalog_name": "e2e-test",
                "instruments": [
                    {
                        "ticker": "AAPL",
                        "nautilus_id": "AAPL.NASDAQ",
                        "asset_class": "STOCK",
                        "catalog_instrument_id": 1,
                        "date_range_start": "<ISO-8601>",
                        "date_range_end": "<ISO-8601>",
                        "timeframes": {
                            "1-DAY":    {"bar_count": 6601},
                            "1-HOUR":   {"bar_count": 89378},
                            "5-MINUTE": {"bar_count": 864403},
                            "1-MINUTE": {"bar_count": 3439333}
                        }
                    },
                    ...
                ]
            }
        ]
    }

The flattened ``(catalog, ticker, timeframe)`` view is reconstructed by callers
that want the 20-entry shape; the on-disk model stores per-timeframe counts as
columns of the single per-ticker row.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from src.db.models.catalog_instrument import CatalogInstrument  # noqa: E402
from src.db.session_sync import get_sync_session  # noqa: E402

logger = structlog.get_logger(__name__)


def _serialize_instrument(row: CatalogInstrument) -> dict:
    return {
        "ticker": row.ticker,
        "nautilus_id": row.nautilus_id,
        "asset_class": row.asset_class,
        "catalog_instrument_id": row.id,
        "date_range_start": row.date_range_start.isoformat() if row.date_range_start else None,
        "date_range_end": row.date_range_end.isoformat() if row.date_range_end else None,
        "timeframes": {
            "1-DAY": {"bar_count": row.bar_count_daily},
            "1-HOUR": {"bar_count": row.bar_count_hourly},
            "5-MINUTE": {"bar_count": row.bar_count_5min},
            "1-MINUTE": {"bar_count": row.bar_count_minute},
        },
    }


def snapshot(catalog_names: list[str] | None = None) -> dict:
    """Build a snapshot dict for the requested catalogs.

    Args:
        catalog_names: Catalogs to include. ``None`` (or empty) snapshots
            every catalog that has rows in ``catalog_instruments``.

    Returns:
        Snapshot dict shaped per this module's docstring.
    """
    snapshot_dict: dict = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "catalogs": [],
    }

    with get_sync_session() as session:
        stmt = select(CatalogInstrument)
        if catalog_names:
            stmt = stmt.where(CatalogInstrument.catalog_name.in_(catalog_names))
        stmt = stmt.order_by(CatalogInstrument.catalog_name, CatalogInstrument.ticker)
        rows = session.execute(stmt).scalars().all()

        by_catalog: dict[str, list[CatalogInstrument]] = {}
        for row in rows:
            by_catalog.setdefault(row.catalog_name, []).append(row)

        # Preserve requested catalog order when provided; otherwise sort.
        order = catalog_names if catalog_names else sorted(by_catalog.keys())
        for name in order:
            if name not in by_catalog:
                logger.warning("snapshot_catalog_empty", catalog_name=name)
                snapshot_dict["catalogs"].append({"catalog_name": name, "instruments": []})
                continue
            snapshot_dict["catalogs"].append(
                {
                    "catalog_name": name,
                    "instruments": [_serialize_instrument(r) for r in by_catalog[name]],
                }
            )

    return snapshot_dict


def write_snapshot(data: dict, output_path: Path) -> None:
    """Write ``data`` to ``output_path`` as pretty-printed JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True))
    logger.info(
        "snapshot_written",
        output=str(output_path),
        catalog_count=len(data.get("catalogs", [])),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--catalog",
        action="append",
        dest="catalogs",
        default=None,
        help="Catalog name to snapshot (repeatable). Omit to snapshot all.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the JSON snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    data = snapshot(args.catalogs)
    write_snapshot(data, args.output)

    # Friendly stdout summary so an operator running the script sees results.
    total_entries = 0
    for cat in data["catalogs"]:
        n = len(cat["instruments"])
        total_entries += n
        print(f"  {cat['catalog_name']}: {n} instruments")
    print(f"Wrote snapshot to {args.output} ({total_entries} instruments total)")


if __name__ == "__main__":
    main()
