"""Resolve ETF venues against IBKR's contract database.

Closes the Phase 2 venue gate. FMP's conservative map (ADR-6) leaves most of the
ETF universe VENUE_UNRESOLVED because it only maps NASDAQ and NYSE and refuses to
guess from the ambiguous AMEX label. IBKR's contract database answers the question
FMP cannot, and it is the same source the Nautilus IB adapter qualifies against.

Output is a proposed ``venue_overrides.csv``, not a direct database write. That
reuses the tested override path and leaves every venue decision as a reviewable,
git-tracked line — the PRD's "resolved manually, never inferred", satisfied by
recording an authoritative lookup as a decision rather than applying it silently.

Requires IB Gateway or TWS to be running (paper is fine — this reads the contract
database, it does not trade).

Usage:
    # See the plan without connecting to anything
    uv run python scripts/venue/resolve_venues_ibkr.py --catalog firstrate-etf --dry-run

    # Smoke test against known-answer tickers
    uv run python scripts/venue/resolve_venues_ibkr.py \\
        --tickers SPY,GLD,IWM,QQQ,IVV,ZZZZZQ --state-dir logs/venue-backfill/smoke

    # The full sweep (safe to Ctrl-C and re-run — it resumes)
    uv run python scripts/venue/resolve_venues_ibkr.py --catalog firstrate-etf \\
        --include-resolved --state-dir logs/venue-backfill/run1

    # Fold the results into the tracked override file
    uv run python scripts/venue/resolve_venues_ibkr.py \\
        --state-dir logs/venue-backfill/run1 --merge-overrides venue_overrides.csv

Exit codes:
    0  complete
    1  fatal error
    2  database could not be evaluated
    3  partial but resumable (interrupted, or the reconnect budget ran out)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Running a script by path puts its own directory on sys.path, not the repo root,
# so `src` would not import. Insert the root before any `src.*` import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Load .env before importing anything that reads settings at module scope.
load_dotenv()

from src.db.repositories.instrument_metadata_repository_sync import (  # noqa: E402
    SyncInstrumentMetadataRepository,
)
from src.db.session_sync import get_sync_session  # noqa: E402
from src.services.ibkr_client import IBKRHistoricalClient  # noqa: E402
from src.services.metadata.providers.ibkr_venue_provider import (  # noqa: E402
    IBKRVenueProvider,
    VenueQualification,
)
from src.services.metadata.venue_backfill import (  # noqa: E402
    BackfillConfig,
    CheckpointWriter,
    load_checkpoint,
    load_worklist,
    run_backfill,
    select_pending,
)
from src.services.metadata.venue_backfill_report import (  # noqa: E402
    merge_into_overrides,
    write_disagreements_csv,
    write_overrides_proposal,
    write_rejects_csv,
    write_results_csv,
)

EXIT_OK, EXIT_FATAL, EXIT_DB, EXIT_PARTIAL = 0, 1, 2, 3


def _env_str(name: str, default: str) -> str:
    """Read an env var, stripping the inline ``# comment`` .env files often carry."""
    return os.environ.get(name, default).split("#")[0].strip()


def _env_int(name: str, default: int) -> int:
    return int(_env_str(name, str(default)))


def _say(message: str) -> None:
    print(message, flush=True)


def _load_universe(include_resolved: bool) -> tuple[list[str], dict[str, Optional[str]]]:
    """Read the worklist and the current venue of every ticker, in one session."""
    with get_sync_session() as session:
        repo = SyncInstrumentMetadataRepository(session)
        tickers = load_worklist(repo, include_resolved=include_resolved)
        current_venues = {row.ticker: row.venue for row in repo.list_all()}
    return tickers, current_venues


def _write_reports(state_dir: Path, all_results: list, current_venues: dict) -> None:
    """Emit the four artefacts and tell the operator where they landed."""
    counts = {
        "results.csv": write_results_csv(state_dir / "results.csv", all_results),
        "rejects.csv": write_rejects_csv(state_dir / "rejects.csv", all_results),
        "overrides-proposal.csv": write_overrides_proposal(
            state_dir / "overrides-proposal.csv", all_results
        ),
        "disagreements.csv": write_disagreements_csv(
            state_dir / "disagreements.csv", all_results, current_venues
        ),
    }
    _say("")
    for name, count in counts.items():
        _say(f"  {state_dir / name}  ({count} row(s))")
    if counts["rejects.csv"]:
        _say(f"\n  {counts['rejects.csv']} ticker(s) need manual adjudication — see rejects.csv")
    if counts["disagreements.csv"]:
        _say(
            f"  {counts['disagreements.csv']} ticker(s) where IBKR contradicts the "
            f"recorded venue — see disagreements.csv"
        )


async def _connect(args) -> IBKRHistoricalClient:
    """Connect to IB Gateway, letting connect() own its own timeout.

    Deliberately NOT wrapped in ``asyncio.wait_for``: connect() rotates through
    client_ids internally when one is held by a killed prior process, and an outer
    timeout cancels that loop after the first attempt — defeating the recovery.
    (scripts/diagnostics/ibkr_reconnect_probe.py has exactly that bug.)
    """
    host = _env_str("IBKR_HOST", "127.0.0.1")
    port = _env_int("IBKR_PORT", 4002)
    client_id = args.client_id if args.client_id is not None else _env_int("IBKR_CLIENT_ID", 10)
    _say(f"Connecting to IBKR at {host}:{port} (client_id={client_id})...")
    client = IBKRHistoricalClient(host=host, port=port, client_id=client_id)
    info = await client.connect(timeout=30, max_id_rotations=5)
    _say(f"Connected (account={info.get('account_id')}, client_id={info.get('client_id')})")
    return client


def _progress_printer(every: int = 25):
    """Report progress on a cadence — a silent 30-minute run looks like a hang."""

    def report(done: int, total: int, tally) -> None:
        if done % every == 0 or done == total:
            _say(
                f"  [{done}/{total}] resolved={tally.resolved} not_found={tally.not_found} "
                f"ambiguous={tally.ambiguous} error={tally.error}"
            )

    return report


async def _run(args) -> int:
    state_dir = Path(args.state_dir)
    config = BackfillConfig(
        state_dir=state_dir,
        rate_per_s=args.rate,
        concurrency=args.concurrency,
        flush_every=args.flush_every,
        limit=args.limit,
        retry_errors=args.retry_errors,
        max_reconnects=args.max_reconnects,
        include_resolved=args.include_resolved,
    )

    # --- worklist -----------------------------------------------------------
    current_venues: dict[str, Optional[str]] = {}
    if args.tickers:
        tickers = sorted({t.strip().upper() for t in args.tickers.split(",") if t.strip()})
    else:
        try:
            tickers, current_venues = _load_universe(args.include_resolved)
        except Exception as exc:  # noqa: BLE001
            _say(f"Metadata DB not available — {exc}")
            _say("RESULT: fail reason=db_unavailable")
            return EXIT_DB

    done = load_checkpoint(config.checkpoint_path)
    pending = select_pending(tickers, done, retry_errors=args.retry_errors, limit=args.limit)
    skipped = len(tickers) - len(pending)

    _say(f"Worklist: {len(tickers)} ticker(s) — {len(pending)} to attempt, {skipped} already done")
    if args.dry_run:
        eta = len(pending) / args.rate if args.rate > 0 else 0
        _say(f"Estimated issue time at {args.rate}/s: {eta / 60:.1f} min")
        _say(f"Checkpoint: {config.checkpoint_path}")
        _say("RESULT: ok dry_run=1")
        return EXIT_OK

    if not pending:
        _say("Nothing to do — every ticker already has a decision.")
        if done:
            _write_reports(state_dir, list(done.values()), current_venues)
        _say("RESULT: ok resolved=0 not_found=0 ambiguous=0 error=0 reconnects=0 elapsed=0.0")
        return EXIT_OK

    # --- connect and sweep --------------------------------------------------
    stop_flag = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Drain in-flight work and flush rather than dying mid-write.
        loop.add_signal_handler(sig, stop_flag.set)

    started = time.monotonic()
    client: Optional[IBKRHistoricalClient] = None
    try:
        client = await _connect(args)
        holder = {"client": client}

        async def fetch(contract):
            return await holder["client"].fetch_contract_details(contract)

        async def reconnect() -> None:
            old = holder["client"]
            try:
                await old.disconnect()
            except Exception as exc:  # noqa: BLE001
                _say(f"  (disconnect during reconnect failed: {exc})")
            holder["client"] = await _connect(args)

        provider = IBKRVenueProvider(fetch_details=fetch)
        state_dir.mkdir(parents=True, exist_ok=True)
        with CheckpointWriter(config.checkpoint_path, flush_every=config.flush_every) as writer:
            tally, _ = await run_backfill(
                tickers=pending,
                qualifier=provider,
                config=config,
                writer=writer,
                reconnect=reconnect,
                stop_flag=stop_flag,
                already_done=skipped,
                on_progress=_progress_printer(),
            )
    except ConnectionError as exc:
        _say(f"Could not reach IB Gateway — {exc}")
        _say("RESULT: fail reason=ibkr_unreachable")
        return EXIT_FATAL
    finally:
        if client is not None:
            # Release the client_id gracefully, or the next run pays for rotation.
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # --- reports ------------------------------------------------------------
    all_results = list(load_checkpoint(config.checkpoint_path).values())
    _write_reports(state_dir, all_results, current_venues)
    _say("")
    _say(tally.as_result_line(time.monotonic() - started))
    return EXIT_PARTIAL if tally.stopped_early else EXIT_OK


def _merge_only(args) -> int:
    """Fold an existing run's proposal into the tracked override file."""
    state_dir = Path(args.state_dir)
    results: list[VenueQualification] = list(
        load_checkpoint(state_dir / "checkpoint.jsonl").values()
    )
    if not results:
        _say(f"No checkpoint found in {state_dir} — nothing to merge.")
        return EXIT_FATAL

    proposal_path = state_dir / "overrides-proposal.csv"
    write_overrides_proposal(proposal_path, results)
    from src.services.metadata.venue_overrides import load_venue_overrides

    proposal = load_venue_overrides(proposal_path)
    target = Path(args.merge_overrides)

    if args.dry_run:
        existing = load_venue_overrides(target)
        new = {t for t in proposal if t not in existing}
        conflicts = {t for t, v in proposal.items() if t in existing and existing[t] != v}
        _say(f"Would merge {len(proposal)} proposed row(s) into {target}")
        _say(f"  new: {len(new)}  unchanged: {len(proposal) - len(new) - len(conflicts)}")
        _say(f"  conflicts (existing kept): {len(conflicts)}")
        _say("RESULT: ok dry_run=1")
        return EXIT_OK

    report = merge_into_overrides(target, proposal)
    _say(
        f"Merged into {target}: {report.added} added, {report.unchanged} unchanged, "
        f"{len(report.conflicts)} conflict(s) — {report.total} row(s) total"
    )
    if report.backup:
        _say(f"  backup: {report.backup}")
    for ticker, (existing, proposed) in sorted(report.conflicts.items()):
        _say(f"  conflict {ticker}: kept {existing}, proposed {proposed}")
    _say(f"RESULT: ok merged={report.added} conflicts={len(report.conflicts)}")
    return EXIT_OK


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve ETF venues against IBKR's contract database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--catalog", default=None, help="Catalog name (reporting only).")
    parser.add_argument(
        "--state-dir",
        default="logs/venue-backfill/run1",
        help="Checkpoint + report directory (default: logs/venue-backfill/run1).",
    )
    parser.add_argument("--rate", type=float, default=6.0, help="Requests per second.")
    parser.add_argument("--concurrency", type=int, default=4, help="In-flight requests.")
    parser.add_argument("--flush-every", type=int, default=50, help="Checkpoint flush cadence.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N new attempts.")
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers (skips the DB).")
    parser.add_argument(
        "--include-resolved",
        action="store_true",
        help="Also re-verify tickers FMP already resolved.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the plan; no IBKR connection, no writes."
    )
    parser.add_argument(
        "--retry-errors",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Re-attempt tickers checkpointed as ERROR.",
    )
    parser.add_argument("--max-reconnects", type=int, default=10)
    parser.add_argument("--client-id", type=int, default=None, help="Override IBKR_CLIENT_ID.")
    parser.add_argument(
        "--merge-overrides",
        default=None,
        metavar="PATH",
        help="Merge this run's proposal into PATH and exit (no IBKR connection).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.merge_overrides:
        return _merge_only(args)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _say("RESULT: partial reason=interrupted")
        return EXIT_PARTIAL
    except Exception as exc:  # noqa: BLE001
        _say(f"RESULT: fail reason={type(exc).__name__} msg={exc}")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
