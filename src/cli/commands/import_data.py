"""CLI command for FirstRate data import with progress and summary."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src.cli.commands.import_reporting import (
    _print_progress_line,
    _print_summary,
    determine_exit_code,
)
from src.models.catalog import AssetClass, DryRunReport, ImportResult
from src.services.firstrate.dry_run import (
    _UNKNOWN_TIMEFRAME,
    build_dry_run_report,
    estimate_parquet_bytes,
    format_bytes,
)
from src.services.firstrate.supplementary_discovery import (
    _discover_supplementary_tickers,
    _find_supplementary_dirs,
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


def _run_import(
    format_name: str,
    catalog: str,
    source_path: Path,
    asset_class: str | None,
    timeframe: str | None,
    dividends_dir: Path | None = None,
    splits_dir: Path | None = None,
) -> int:
    """Execute the import pipeline.

    Args:
        format_name: Data format (e.g., "firstrate").
        catalog: Target catalog name.
        source_path: Source directory path.
        asset_class: Optional asset class filter.
        timeframe: Optional comma-separated timeframes.
        dividends_dir: Explicit dividend source dir (overrides discovery).
        splits_dir: Explicit split source dir (overrides discovery).

    Returns:
        Exit code (0, 1, or 2).
    """
    from src.config import get_settings
    from src.db.repositories.catalog_dividend_repository import (
        SyncCatalogDividendRepository,
    )
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.repositories.catalog_stock_split_repository import (
        SyncCatalogStockSplitRepository,
    )
    from src.db.session_sync import get_sync_session_maker
    from src.services.firstrate.catalog_manager import CatalogManager
    from src.services.firstrate.import_service import ImportService
    from src.services.firstrate.instrument_mapper import InstrumentMapper
    from src.services.firstrate.metadata_service import MetadataService
    from src.services.firstrate.supplementary_loader import (
        SupplementaryDataLoader,
        SupplementaryLoadResult,
    )

    settings = get_settings()

    # Resolve supplementary dirs: explicit flags win over auto-discovery (AC-6).
    discovered_div, discovered_split = _find_supplementary_dirs(source_path)
    dividends_dir = dividends_dir or discovered_div
    splits_dir = splits_dir or discovered_split

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
                "❌ Database not configured. Check DATABASE_URL in .env",
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
                    f"❌ Instrument profiles not loaded for catalog "
                    f"'{catalog}' and no company_profiles.csv found at "
                    f"{source_path} or {source_path.parent}",
                    style="red",
                )
                return 2

        # Load supplementary data (dividends/splits) once per invocation,
        # after profiles and before the per-timeframe bar loop (mirrors how
        # company profiles load once). Idempotent set-replace makes this safe
        # to re-run; per-ticker failures are isolated inside the loader (AC-3).
        supplementary_results: list[SupplementaryLoadResult] = []
        if dividends_dir is not None or splits_dir is not None:
            supp_tickers = _discover_supplementary_tickers(dividends_dir, splits_dir)
            if supp_tickers:
                loader = SupplementaryDataLoader(
                    dividend_repo=SyncCatalogDividendRepository(session),
                    split_repo=SyncCatalogStockSplitRepository(session),
                )
                click.echo(f"Loading supplementary data for {len(supp_tickers)} ticker(s)...")
                supplementary_results = loader.load_for_tickers(
                    supp_tickers, catalog, dividends_dir, splits_dir
                )

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
        console.print(f"❌ {e}", style="red")
        if session is not None:
            session.rollback()
        return 2
    except ValueError as e:
        console.print(f"❌ {e}", style="red")
        if session is not None:
            session.rollback()
        return 2
    except Exception as e:
        console.print(f"❌ Fatal error: {e}", style="red")
        if session is not None:
            session.rollback()
        return 2
    finally:
        if session is not None:
            session.close()

    # Summary
    _print_summary(all_results, supplementary_results)

    # Supplementary outcomes are informational only — a supplementary failure
    # must never flip the exit code (AC-3/AC-6); only bar results decide it.
    return determine_exit_code(all_results)


def _print_dry_run_report(report: DryRunReport) -> None:
    """Render a :class:`DryRunReport` to the terminal with Rich.

    Always prints a ``"No data written — dry run only."`` trailer so operators
    have explicit confirmation that nothing touched the catalog or DB.
    """
    console.print()
    header = f"[bold]Dry-Run Report[/bold] — {report.asset_class.value} @ {report.source_path}"
    if report.catalog:
        header += f" → catalog [cyan]{report.catalog}[/cyan]"
    console.print(header)

    # Empty / all-unknown guard: warn loudly rather than printing a blank table.
    countable_specs = [spec for spec in report.timeframes if spec != _UNKNOWN_TIMEFRAME]
    if report.total_file_count == 0 and not report.schema_mismatches:
        console.print("[yellow]No importable files found. Double-check the source path.[/yellow]")
        click.echo("No data written — dry run only.")
        return
    if not countable_specs and report.total_file_count > 0:
        console.print(
            "[yellow]All discovered files fell into the 'unknown' timeframe bucket "
            "— check the filename suffixes.[/yellow]"
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

    click.echo("No data written — dry run only.")


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
            f"❌ --format '{format_name}' is not supported by --dry-run (only 'firstrate').",
            style="red",
        )
        return 2

    if not source_path.exists() or not source_path.is_dir():
        console.print(
            f"❌ Source path does not exist or is not a directory: {source_path}",
            style="red",
        )
        return 2

    ac = ASSET_CLASS_MAP.get((asset_class or "stock").lower(), AssetClass.STOCK)

    try:
        report = build_dry_run_report(source_path, ac, catalog=catalog)
    except (PermissionError, OSError) as e:
        console.print(f"❌ Fatal filesystem error: {e}", style="red")
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
    "--dividends-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory of FirstRate {TICKER}_divs.txt files (auto-discovered if omitted).",
)
@click.option(
    "--splits-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory of FirstRate {TICKER}.txt split files (auto-discovered if omitted).",
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
    dividends_dir: Path | None,
    splits_dir: Path | None,
    dry_run: bool,
    source_path: Path,
) -> None:
    """Import market data from external sources to Parquet catalog.

    SOURCE_PATH is the directory containing ticker data files.
    """
    if dry_run:
        exit_code = _run_dry_run(format_name, catalog, source_path, asset_class)
    else:
        exit_code = _run_import(
            format_name,
            catalog,
            source_path,
            asset_class,
            timeframe,
            dividends_dir,
            splits_dir,
        )
    if exit_code != 0:
        click.get_current_context().exit(exit_code)
