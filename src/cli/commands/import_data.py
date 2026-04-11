"""CLI command for FirstRate data import with progress and summary."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.models.catalog import AssetClass, DryRunReport, ImportResult
from src.services.firstrate.dry_run import (
    _UNKNOWN_TIMEFRAME,
    build_dry_run_report,
    estimate_parquet_bytes,
    format_bytes,
)

console = Console()

TIMEFRAME_MAP = {
    "daily": "1-DAY-LAST",
    "hourly": "1-HOUR-LAST",
    "minute": "1-MINUTE-LAST",
    "1min": "1-MINUTE-LAST",
    "5min": "5-MINUTE-LAST",
}

ASSET_CLASS_MAP = {
    "etf": AssetClass.ETF,
    "stock": AssetClass.STOCK,
    "futures": AssetClass.FUTURES,
    "fx": AssetClass.FX,
    "crypto": AssetClass.CRYPTO,
    "index": AssetClass.INDEX,
}


def _find_profiles_csv(source_path: Path) -> Path | None:
    """Locate the FirstRate company_profiles.csv for a given import source.

    FirstRate deliveries usually ship the profiles file alongside the data
    folders (e.g., ``/Data/Stocks/company_profiles.csv`` for
    ``/Data/Stocks/Stocks_1day``). Check ``source_path`` first, then its
    parent directory.

    Args:
        source_path: Directory passed to the import command.

    Returns:
        Path to the profiles CSV, or None if not found in either location.
    """
    candidates = [source_path / "company_profiles.csv", source_path.parent / "company_profiles.csv"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def parse_timeframes(timeframe_str: str) -> list[str]:
    """Parse comma-separated timeframe string into Nautilus specs.

    Args:
        timeframe_str: Comma-separated timeframes (e.g., "daily,hourly").

    Returns:
        List of Nautilus timeframe specs (e.g., ["1-DAY-LAST", "1-HOUR-LAST"]).

    Raises:
        click.BadParameter: If any timeframe name is not recognized.
    """
    seen: set[str] = set()
    specs: list[str] = []
    for name in timeframe_str.split(","):
        name = name.strip().lower()
        if name not in TIMEFRAME_MAP:
            raise click.BadParameter(
                f"Unknown timeframe '{name}'. Valid: {', '.join(TIMEFRAME_MAP.keys())}"
            )
        spec = TIMEFRAME_MAP[name]
        if spec not in seen:
            seen.add(spec)
            specs.append(spec)
    return specs


def determine_exit_code(results: list[ImportResult]) -> int:
    """Determine CLI exit code from import results.

    Args:
        results: List of import results.

    Returns:
        0 if all success, 1 if any failures.
    """
    if any(r.status == "failed" for r in results):
        return 1
    return 0


def build_summary_text(results: list[ImportResult]) -> str:
    """Build plain-text summary of import results.

    Args:
        results: List of import results.

    Returns:
        Formatted summary string.
    """
    total = len(results)
    successes = sum(1 for r in results if r.status == "success")
    failures = sum(1 for r in results if r.status == "failed")
    total_rows = sum(r.row_count for r in results)

    lines = [
        "",
        "Import Summary",
        "\u2501" * 27,
        f"Total tickers: {total}",
        f"Successful:    {successes}",
        f"Failed:        {failures}",
        f"Total rows:    {total_rows:,}",
    ]

    failed_results = [r for r in results if r.status == "failed"]
    if failed_results:
        lines.append("")
        lines.append("Failures:")
        for r in failed_results:
            lines.append(f"  {r.ticker} \u2014 {r.error}")

    return "\n".join(lines)


def _print_progress_line(result: ImportResult, timeframe: str) -> None:
    """Print a single ticker progress line.

    Args:
        result: Import result for one ticker.
        timeframe: Timeframe spec used for this import.
    """
    if result.status == "success":
        click.echo(f"{result.ticker} \u2014 {timeframe} \u2014 {result.row_count:,} rows \u2713")
    else:
        click.echo(
            f"{result.ticker} \u2014 {timeframe} \u2014 {result.error or 'Unknown error'} \u2717"
        )


def _print_summary(results: list[ImportResult]) -> None:
    """Print Rich-formatted summary table.

    Args:
        results: All import results across timeframes.
    """
    total = len(results)
    successes = sum(1 for r in results if r.status == "success")
    failures = sum(1 for r in results if r.status == "failed")
    total_rows = sum(r.row_count for r in results)

    console.print()
    table = Table(title="Import Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total tickers", str(total))
    table.add_row("Successful", str(successes))
    table.add_row("Failed", str(failures))
    table.add_row("Total rows", f"{total_rows:,}")
    console.print(table)

    failed_results = [r for r in results if r.status == "failed"]
    if failed_results:
        console.print()
        fail_table = Table(title="Failures")
        fail_table.add_column("Ticker", style="red")
        fail_table.add_column("Reason", style="yellow")
        for r in failed_results:
            fail_table.add_row(r.ticker, r.error or "Unknown error")
        console.print(fail_table)


def _run_import(
    format_name: str,
    catalog: str,
    source_path: Path,
    asset_class: str | None,
    timeframe: str | None,
) -> int:
    """Execute the import pipeline.

    Args:
        format_name: Data format (e.g., "firstrate").
        catalog: Target catalog name.
        source_path: Source directory path.
        asset_class: Optional asset class filter.
        timeframe: Optional comma-separated timeframes.

    Returns:
        Exit code (0, 1, or 2).
    """
    from src.config import get_settings
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session_maker
    from src.services.firstrate.catalog_manager import CatalogManager
    from src.services.firstrate.import_service import ImportService
    from src.services.firstrate.instrument_mapper import InstrumentMapper
    from src.services.firstrate.metadata_service import MetadataService

    settings = get_settings()

    # Parse asset class (default to ETF for FirstRate)
    ac = ASSET_CLASS_MAP.get((asset_class or "etf").lower(), AssetClass.ETF)

    # Parse timeframes (default to daily)
    timeframes = parse_timeframes(timeframe or "daily")

    # Wire dependencies
    session = None
    try:
        session_maker = get_sync_session_maker()
        if session_maker is None:
            console.print(
                "\u274c Database not configured. Check DATABASE_URL in .env",
                style="red",
            )
            return 2
        session = session_maker()

        catalog_base = Path(settings.catalog.catalog_base_path)
        catalog_manager = CatalogManager(catalog_base)
        sync_repo = SyncCatalogInstrumentRepository(session)
        metadata_service = MetadataService(sync_repo=sync_repo)
        instrument_mapper = InstrumentMapper(sync_repo)
        import_service = ImportService(catalog_manager, metadata_service, instrument_mapper)

        # Ensure profiles are loaded
        if not instrument_mapper.is_loaded(catalog):
            profiles_path = _find_profiles_csv(source_path)
            if profiles_path is not None:
                click.echo(f"Loading company profiles from {profiles_path}...")
                instrument_mapper.load_company_profiles(profiles_path, catalog, ac.value)
            else:
                console.print(
                    f"\u274c Instrument profiles not loaded for catalog "
                    f"'{catalog}' and no company_profiles.csv found at "
                    f"{source_path} or {source_path.parent}",
                    style="red",
                )
                return 2

        # Run import for each timeframe
        all_results: list[ImportResult] = []
        click.echo(f"\n--- {ac.value} ---")
        for tf in timeframes:
            results = import_service.import_directory(
                source_dir=source_path,
                catalog_name=catalog,
                asset_class=ac,
                timeframe=tf,
            )

            for r in results:
                _print_progress_line(r, tf)

            all_results.extend(results)

        # Commit all profile loads and metadata upserts as a single transaction.
        session.commit()

    except FileNotFoundError as e:
        console.print(f"\u274c {e}", style="red")
        if session is not None:
            session.rollback()
        return 2
    except ValueError as e:
        console.print(f"\u274c {e}", style="red")
        if session is not None:
            session.rollback()
        return 2
    except Exception as e:
        console.print(f"\u274c Fatal error: {e}", style="red")
        if session is not None:
            session.rollback()
        return 2
    finally:
        if session is not None:
            session.close()

    # Summary
    _print_summary(all_results)

    return determine_exit_code(all_results)


def _print_dry_run_report(report: DryRunReport) -> None:
    """Render a :class:`DryRunReport` to the terminal with Rich.

    Always prints a ``"No data written — dry run only."`` trailer so operators
    have explicit confirmation that nothing touched the catalog or DB.
    """
    console.print()
    header = f"[bold]Dry-Run Report[/bold] \u2014 {report.asset_class.value} @ {report.source_path}"
    if report.catalog:
        header += f" \u2192 catalog [cyan]{report.catalog}[/cyan]"
    console.print(header)

    # Empty / all-unknown guard: warn loudly rather than printing a blank table.
    countable_specs = [spec for spec in report.timeframes if spec != _UNKNOWN_TIMEFRAME]
    if report.total_file_count == 0 and not report.schema_mismatches:
        console.print("[yellow]No importable files found. Double-check the source path.[/yellow]")
        click.echo("No data written \u2014 dry run only.")
        return
    if not countable_specs and report.total_file_count > 0:
        console.print(
            "[yellow]All discovered files fell into the 'unknown' timeframe bucket "
            "\u2014 check the filename suffixes.[/yellow]"
        )

    table = Table()
    table.add_column("Timeframe", style="cyan")
    table.add_column("Tickers", style="green", justify="right")
    table.add_column("Files", style="green", justify="right")
    table.add_column("Source Size", style="green", justify="right")
    table.add_column("Est. Parquet Size", style="green", justify="right")

    for spec in sorted(report.timeframes.keys()):
        summary = report.timeframes[spec]
        table.add_row(
            spec,
            str(summary.ticker_count),
            str(summary.file_count),
            format_bytes(summary.source_bytes),
            format_bytes(estimate_parquet_bytes(summary.source_bytes)),
        )

    table.add_row(
        "TOTAL",
        str(report.distinct_ticker_count),
        str(report.total_file_count),
        format_bytes(report.total_source_bytes),
        format_bytes(report.estimated_parquet_bytes),
    )
    console.print(table)

    if report.unreadable_count:
        console.print(
            f"[yellow]Warning: {report.unreadable_count} file(s) or director(y/ies) "
            "could not be read (permission denied or I/O error) and were skipped.[/yellow]"
        )

    if report.schema_mismatches:
        console.print()
        mismatch_table = Table(title="Schema Mismatches")
        mismatch_table.add_column("File", style="red")
        mismatch_table.add_column("Expected", style="yellow", justify="right")
        mismatch_table.add_column("Detected", style="yellow", justify="right")
        mismatch_table.add_column("Reason", style="yellow")

        limit = 20
        for mismatch in report.schema_mismatches[:limit]:
            mismatch_table.add_row(
                str(mismatch.file_path),
                str(mismatch.expected_columns),
                str(mismatch.detected_columns),
                mismatch.reason or "",
            )
        console.print(mismatch_table)

        remaining = len(report.schema_mismatches) - limit
        if remaining > 0:
            console.print(f"... and {remaining} more")

    click.echo("No data written \u2014 dry run only.")


def _run_dry_run(
    format_name: str,
    catalog: str,
    source_path: Path,
    asset_class: str | None,
) -> int:
    """Execute a dry-run scan without writing to the catalog or DB.

    Zero side effects: does NOT construct ``ImportService``, ``CatalogManager``,
    ``MetadataService``, ``InstrumentMapper``, or any DB session. Defaults
    asset class to ``STOCK`` (Phase 1 pivot 2026-04-11). Mismatches are
    informational — exit code stays 0 per AC-4.

    Args:
        format_name: Data format. Only ``"firstrate"`` is supported; any other
            value is rejected with exit code 2 so the dry-run does not silently
            scan with the wrong parser assumptions.
        catalog: Target catalog name. Never touched (the dry-run writes
            nothing) but surfaced in the report header so operators can
            confirm they targeted the right catalog.
        source_path: Source directory path.
        asset_class: Optional asset-class override (defaults to ``stock``).

    Returns:
        0 on success (even with schema mismatches); 2 on fatal path errors or
        an unsupported ``format_name``.
    """
    if format_name.lower() != "firstrate":
        console.print(
            f"\u274c --format '{format_name}' is not supported by --dry-run (only 'firstrate').",
            style="red",
        )
        return 2

    if not source_path.exists() or not source_path.is_dir():
        console.print(
            f"\u274c Source path does not exist or is not a directory: {source_path}",
            style="red",
        )
        return 2

    ac = ASSET_CLASS_MAP.get((asset_class or "stock").lower(), AssetClass.STOCK)

    try:
        report = build_dry_run_report(source_path, ac, catalog=catalog)
    except (PermissionError, OSError) as e:
        console.print(f"\u274c Fatal filesystem error: {e}", style="red")
        return 2

    _print_dry_run_report(report)
    return 0


@click.command("import")
@click.option(
    "--format",
    "format_name",
    type=click.Choice(["firstrate"], case_sensitive=False),
    required=True,
    help="Data source format.",
)
@click.option(
    "--catalog",
    required=True,
    help="Target catalog name.",
)
@click.option(
    "--asset-class",
    type=click.Choice(
        ["etf", "stock", "futures", "fx", "crypto", "index"],
        case_sensitive=False,
    ),
    default=None,
    help="Filter by asset class (default: etf for import, stock for --dry-run).",
)
@click.option(
    "--timeframe",
    default=None,
    help="Comma-separated timeframes: daily,hourly,minute,1min,5min.",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    help="Scan the source directory and report what would be imported without writing any data.",
)
@click.argument(
    "source_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def import_firstrate(
    format_name: str,
    catalog: str,
    asset_class: str | None,
    timeframe: str | None,
    dry_run: bool,
    source_path: Path,
) -> None:
    """Import market data from external sources to Parquet catalog.

    SOURCE_PATH is the directory containing ticker data files.
    """
    if dry_run:
        exit_code = _run_dry_run(format_name, catalog, source_path, asset_class)
    else:
        exit_code = _run_import(format_name, catalog, source_path, asset_class, timeframe)
    if exit_code != 0:
        click.get_current_context().exit(exit_code)
