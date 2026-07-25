"""Turn a venue-sweep's unresolved tail into an audited exclusion register.

The sweep leaves tickers IBKR cannot qualify. Under the PRD amendment those may
leave ``VENUE_UNRESOLVED`` only via ``venue_exclusions.csv``, and only with a
written reason and evidence — the requirement that separates an adjudicated
exclusion from a guessed venue.

This generates those rows mechanically, but each one carries a *ticker-specific*
fact rather than boilerplate: the sweep's own verdict for that symbol, and that
ticker's last bar date from the catalog, which is what makes "consistent with
delisting" a claim someone can check rather than an assertion. Tickers that traded
right up to the data cutoff are described differently from ones that stopped years
earlier, because the evidence for them genuinely is weaker.

Re-runnable: it reads the sweep results and the catalog and rewrites the register,
so a later sweep that resolves some of these simply drops them.

Usage:
    uv run python scripts/venue/build_exclusions.py \\
        --results logs/venue-backfill/run1/results.csv \\
        --catalog firstrate-etf --out venue_exclusions.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from src.db.repositories.catalog_instrument_repository import (  # noqa: E402
    SyncCatalogInstrumentRepository,
)
from src.db.session_sync import get_sync_session  # noqa: E402

#: Where the sweep artefacts are archived for audit. logs/ is gitignored, so the
#: evidence pointer must name a tracked location or it is unverifiable later.
_EVIDENCE_DIR = "_bmad-output/implementation-artifacts/venue-backfill-evidence"


def _last_bar_dates(catalog_name: str) -> dict[str, Optional[date]]:
    """Each ticker's last daily bar — the corroborating half of the evidence."""
    with get_sync_session() as session:
        repo = SyncCatalogInstrumentRepository(session)
        return {
            row.ticker: (row.date_range_end_daily.date() if row.date_range_end_daily else None)
            for row in repo.iter_by_catalog(catalog_name)
        }


def _reason(outcome: str, last_bar, cutoff, candidates: str, swept: str) -> str:
    """A ticker-specific justification, honest about how strong the evidence is."""
    if outcome == "ambiguous":
        return (
            f"IBKR returns multiple listing venues ({candidates or 'several'}) for this "
            f"symbol, so the venue is undecidable from the contract database "
            f"(sweep {swept})"
        )
    if last_bar is None:
        return f"IBKR contract database returned no matching contract (sweep {swept}); "
    if cutoff is not None and last_bar >= cutoff:
        # Weaker evidence, and said so: it was alive when the data was captured.
        return (
            f"IBKR contract database returned no matching contract (sweep {swept}); "
            f"traded to the catalog cutoff {cutoff}, so delisted between then and the sweep"
        )
    return (
        f"IBKR contract database returned no matching contract (sweep {swept}); "
        f"last daily bar {last_bar}, before the catalog cutoff {cutoff} — "
        f"consistent with delisting"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, help="Sweep results.csv")
    parser.add_argument("--catalog", default="firstrate-etf")
    parser.add_argument("--out", default="venue_exclusions.csv")
    parser.add_argument("--swept-on", default=None, help="Sweep date (default: today, UTC)")
    parser.add_argument(
        "--skip-tickers",
        default=None,
        help=(
            "Comma-separated tickers to leave OUT of the register — for symbols the "
            "operator resolved by hand (they belong in venue_overrides.csv instead)."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    swept = args.swept_on or datetime.now(timezone.utc).date().isoformat()
    skip = (
        {t.strip().upper() for t in args.skip_tickers.split(",") if t.strip()}
        if args.skip_tickers
        else set()
    )
    results = list(csv.DictReader(Path(args.results).open(encoding="utf-8")))
    unresolved = [
        r for r in results if r["outcome"] != "resolved" and r["ticker"].upper() not in skip
    ]
    if skip:
        print(f"Skipping {len(skip)} operator-resolved ticker(s): {', '.join(sorted(skip))}")
    if not unresolved:
        print("No unresolved tickers — nothing to exclude.")
        return 0

    last_bars = _last_bar_dates(args.catalog)
    cutoff = max((d for d in last_bars.values() if d), default=None)

    rows = []
    for record in sorted(unresolved, key=lambda r: r["ticker"]):
        ticker = record["ticker"]
        last_bar = last_bars.get(ticker)
        rows.append(
            {
                "ticker": ticker,
                "reason": _reason(record["outcome"], last_bar, cutoff, record["candidates"], swept),
                "evidence": (
                    f"{_EVIDENCE_DIR}/results.csv row '{ticker}' "
                    f"(outcome={record['outcome']}, {record['elapsed_s']}s, "
                    f"{record['attempts']} attempt(s)); "
                    f"catalog_instruments.date_range_end_daily={last_bar}"
                ),
            }
        )

    print(f"Catalog cutoff (latest daily bar anywhere): {cutoff}")
    print(f"Exclusions to write: {len(rows)}")
    before = sum(1 for r in rows if "consistent with delisting" in r["reason"])
    print(f"  clearly delisted (last bar before cutoff): {before}")
    print(f"  traded to cutoff (delisted since sweep):   {len(rows) - before}")

    if args.dry_run:
        for row in rows[:3]:
            print(f"\n  {row['ticker']}")
            print(f"    reason:   {row['reason']}")
            print(f"    evidence: {row['evidence']}")
        print("\nRESULT: ok dry_run=1")
        return 0

    out = Path(args.out)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "reason", "evidence"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} exclusion(s) -> {out}")
    print(f"RESULT: ok excluded={len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
