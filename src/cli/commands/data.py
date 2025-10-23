"""Data management commands."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.services.ibkr_client import IBKRHistoricalClient
from src.services.data_fetcher import HistoricalDataFetcher
from src.config import get_settings
from nautilus_trader.adapters.interactive_brokers.common import IBContract

console = Console()
settings = get_settings()


@click.group()
def data():
    """Data management commands."""
    pass


@data.command("import")
@click.option(
    "--csv",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to CSV file to import",
)
@click.option("--symbol", "-s", required=True, help="Trading symbol (e.g., AAPL)")
@click.option(
    "--venue",
    "-v",
    default="NASDAQ",
    help="Venue/exchange (default: NASDAQ)",
)
@click.option(
    "--bar-type",
    "-b",
    default="1-MINUTE-LAST",
    help="Bar type specification (default: 1-MINUTE-LAST)",
)
@click.option(
    "--conflict-mode",
    type=click.Choice(["skip", "overwrite", "merge"], case_sensitive=False),
    default="skip",
    help="Conflict resolution: skip (default), overwrite, or merge",
)
def import_data(
    csv: Path,
    symbol: str,
    venue: str,
    bar_type: str,
    conflict_mode: str,
):
    """Import CSV market data directly to Parquet catalog."""

    async def import_async():
        # Show progress while importing
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task(f"Importing {csv.name}...", total=None)

            try:
                # Load CSV file using new Parquet-based loader
                from src.services.csv_loader import CSVLoader

                loader = CSVLoader(conflict_mode=conflict_mode.lower())
                result = await loader.load_file(
                    csv, symbol.upper(), venue.upper(), bar_type.upper()
                )

                progress.update(task, completed=True)

                # Display results
                if result["bars_written"] > 0:
                    console.print(
                        f"✅ Successfully imported {result['bars_written']} bars",
                        style="green bold",
                    )
                else:
                    console.print(
                        "⚠️  No bars written",
                        style="yellow",
                    )

                if result["conflicts_skipped"] > 0:
                    console.print(
                        f"⚠️  Skipped {result['conflicts_skipped']} bars (conflict mode: {conflict_mode})",
                        style="yellow",
                    )

                if result["validation_errors"]:
                    console.print(
                        f"\n⚠️  {len(result['validation_errors'])} validation errors:",
                        style="yellow bold",
                    )
                    # Show first 5 errors
                    for error in result["validation_errors"][:5]:
                        console.print(f"   • {error}", style="yellow")
                    if len(result["validation_errors"]) > 5:
                        console.print(
                            f"   • ... and {len(result['validation_errors']) - 5} more errors",
                            style="yellow dim",
                        )

                # Show summary table
                table = Table(title="CSV Import Summary")
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="green")

                table.add_row("File", str(csv))
                table.add_row("Instrument ID", result["instrument_id"])
                table.add_row("Bar Type", result["bar_type_spec"])
                table.add_row("Rows Processed", str(result["rows_processed"]))
                table.add_row("Bars Written", str(result["bars_written"]))
                table.add_row("Conflicts Skipped", str(result["conflicts_skipped"]))
                table.add_row(
                    "Validation Errors", str(len(result["validation_errors"]))
                )
                table.add_row("Date Range", result["date_range"])
                table.add_row("File Size", f"{result['file_size_kb']:.2f} KB")

                console.print(table)

                # Show tips
                if result["bars_written"] > 0:
                    console.print(
                        f"\n💡 Data available for backtests with symbol: {symbol.upper()}",
                        style="cyan",
                    )
                    console.print(
                        f"   Run: ntrader backtest run --symbol {symbol.upper()} --start YYYY-MM-DD --end YYYY-MM-DD",
                        style="cyan dim",
                    )

                return result["bars_written"] > 0

            except FileNotFoundError:
                progress.update(task, completed=True)
                console.print(f"❌ File not found: {csv}", style="red")
                return False
            except Exception as e:
                progress.update(task, completed=True)
                console.print(f"❌ Import failed: {e}", style="red")
                import traceback

                console.print(traceback.format_exc(), style="red dim")
                return False

    # Run async function
    result = asyncio.run(import_async())

    if not result:
        raise click.ClickException("Import failed")


@data.command("list")
@click.option("--symbol", "-s", help="Filter by trading symbol")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["table", "json", "csv"], case_sensitive=False),
    default="table",
    help="Output format (default: table)",
)
def list_data(symbol: Optional[str], format: str):
    """List all available market data in catalog."""
    from src.services.data_catalog import DataCatalogService
    import json

    try:
        catalog_service = DataCatalogService()
        catalog_data = catalog_service.scan_catalog()

        if not catalog_data:
            console.print(
                "📊 No data found in catalog",
                style="yellow",
            )
            console.print(
                "\n💡 Import data with: ntrader data import --csv FILE --symbol SYMBOL",
                style="cyan dim",
            )
            return

        # Filter by symbol if specified
        if symbol:
            symbol_upper = symbol.upper()
            catalog_data = {
                inst_id: avail
                for inst_id, avail in catalog_data.items()
                if symbol_upper in inst_id.upper()
            }

            if not catalog_data:
                console.print(
                    f"📊 No data found for symbol: {symbol_upper}",
                    style="yellow",
                )
                return

        # Output in requested format
        if format.lower() == "json":
            # JSON output
            output = []
            for instrument_id, availabilities in catalog_data.items():
                for avail in availabilities:
                    output.append(
                        {
                            "instrument_id": instrument_id,
                            "bar_type": avail.bar_type_spec,
                            "start_date": avail.start_date.isoformat(),
                            "end_date": avail.end_date.isoformat(),
                            "file_count": avail.file_count,
                            "total_rows": avail.total_rows,
                        }
                    )
            console.print(json.dumps(output, indent=2))

        elif format.lower() == "csv":
            # CSV output
            import csv
            import sys

            writer = csv.writer(sys.stdout)
            writer.writerow(
                [
                    "Instrument ID",
                    "Bar Type",
                    "Start Date",
                    "End Date",
                    "Files",
                    "Rows",
                ]
            )
            for instrument_id, availabilities in catalog_data.items():
                for avail in availabilities:
                    writer.writerow(
                        [
                            instrument_id,
                            avail.bar_type_spec,
                            avail.start_date.strftime("%Y-%m-%d"),
                            avail.end_date.strftime("%Y-%m-%d"),
                            avail.file_count,
                            avail.total_rows,
                        ]
                    )

        else:
            # Table output (default)
            table = Table(
                title=f"Catalog Contents: {catalog_service.catalog_path}",
                show_lines=True,
            )
            table.add_column("Instrument", style="cyan", no_wrap=True)
            table.add_column("Bar Type", style="magenta")
            table.add_column("Date Range", style="green")
            table.add_column("Files", justify="right", style="blue")
            table.add_column("Rows", justify="right", style="yellow")

            total_files = 0
            total_rows = 0

            for instrument_id, availabilities in sorted(catalog_data.items()):
                for avail in sorted(availabilities, key=lambda x: x.bar_type_spec):
                    date_range = (
                        f"{avail.start_date.strftime('%Y-%m-%d')}\n"
                        f"to {avail.end_date.strftime('%Y-%m-%d')}"
                    )
                    table.add_row(
                        instrument_id,
                        avail.bar_type_spec,
                        date_range,
                        str(avail.file_count),
                        f"{avail.total_rows:,}",
                    )
                    total_files += avail.file_count
                    total_rows += avail.total_rows

            console.print(table)

            # Summary
            console.print(
                f"\n📊 Total: {len(catalog_data)} instruments, "
                f"{total_files} files, ~{total_rows:,} bars",
                style="cyan bold",
            )

            # Tips
            console.print(
                "\n💡 Check specific symbol: ntrader data check --symbol AAPL",
                style="cyan dim",
            )

    except Exception as e:
        console.print(f"❌ Failed to list catalog: {e}", style="red")
        raise click.ClickException("List failed")


@data.command("check")
@click.option("--symbol", "-s", required=True, help="Trading symbol to check")
@click.option(
    "--venue", "-v", default="NASDAQ", help="Venue/exchange (default: NASDAQ)"
)
@click.option(
    "--bar-type",
    "-b",
    default="1-MINUTE-LAST",
    help="Bar type to check (default: 1-MINUTE-LAST)",
)
@click.option(
    "--start",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Check for gaps from this date (optional)",
)
@click.option(
    "--end",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Check for gaps until this date (optional)",
)
def check_data(
    symbol: str,
    venue: str,
    bar_type: str,
    start: Optional[datetime],
    end: Optional[datetime],
):
    """Check data availability and detect gaps for specific instrument."""
    from src.services.data_catalog import DataCatalogService

    try:
        catalog_service = DataCatalogService()
        instrument_id = f"{symbol.upper()}.{venue.upper()}"
        bar_type_spec = bar_type.upper()

        availability = catalog_service.get_availability(instrument_id, bar_type_spec)

        if not availability:
            console.print(
                f"❌ No data found for {instrument_id} ({bar_type_spec})",
                style="red bold",
            )
            console.print(
                "\n🔧 Fetch data with:",
                style="cyan",
            )
            console.print(
                f"   ntrader backtest run --symbol {symbol.upper()} --start YYYY-MM-DD --end YYYY-MM-DD",
                style="cyan dim",
            )
            console.print(
                "\nOr import CSV:",
                style="cyan",
            )
            console.print(
                f"   ntrader data import --csv FILE --symbol {symbol.upper()} --venue {venue.upper()}",
                style="cyan dim",
            )
            return

        # Display availability
        console.print(
            f"\n✅ Data Available: {instrument_id}",
            style="green bold",
        )

        table = Table(title=f"Availability: {bar_type_spec}", show_header=False)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Start Date", availability.start_date.strftime("%Y-%m-%d"))
        table.add_row("End Date", availability.end_date.strftime("%Y-%m-%d"))
        table.add_row("File Count", f"{availability.file_count} files")
        table.add_row("Total Rows", f"~{availability.total_rows:,} bars")
        table.add_row(
            "Last Updated",
            availability.last_updated.strftime("%Y-%m-%d %H:%M UTC"),
        )

        console.print(table)

        # Gap detection if date range specified
        if start and end:
            gaps = catalog_service.detect_gaps(instrument_id, bar_type_spec, start, end)

            if gaps:
                console.print(
                    f"\n⚠️  {len(gaps)} gap(s) detected in requested range:",
                    style="yellow bold",
                )
                for i, gap in enumerate(gaps, 1):
                    console.print(
                        f"   {i}. {gap['start'].strftime('%Y-%m-%d')} to {gap['end'].strftime('%Y-%m-%d')}",
                        style="yellow",
                    )

                # Tips
                console.print(
                    "\n💡 Fill gaps by running:",
                    style="cyan",
                )
                console.print(
                    f"   ntrader backtest run --symbol {symbol.upper()} "
                    f"--start {start.strftime('%Y-%m-%d')} "
                    f"--end {end.strftime('%Y-%m-%d')}",
                    style="cyan dim",
                )
                console.print(
                    "\n   (Auto-fetch will download missing data from IBKR)",
                    style="cyan dim",
                )
            else:
                console.print(
                    f"\n✅ No gaps detected in requested range "
                    f"({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')})",
                    style="green",
                )

    except Exception as e:
        console.print(f"❌ Failed to check data: {e}", style="red")
        import traceback

        console.print(traceback.format_exc(), style="red dim")
        raise click.ClickException("Check failed")


@data.command("connect")
@click.option(
    "--host",
    default=None,
    help="IBKR Gateway/TWS host address (defaults to settings)",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="IBKR Gateway/TWS port (defaults to settings)",
)
@click.option(
    "--client-id",
    type=int,
    default=None,
    help="Client ID for connection (defaults to settings)",
)
def connect(host: Optional[str], port: Optional[int], client_id: Optional[int]):
    """Test connection to Interactive Brokers Gateway/TWS."""

    async def connect_async():
        # Use settings or command line overrides
        host_addr = host or settings.ibkr.ibkr_host
        port_num = port or settings.ibkr.ibkr_port
        client_id_num = client_id or settings.ibkr.ibkr_client_id

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Connecting to IBKR...", total=None)

            try:
                # Create client
                client = IBKRHistoricalClient(
                    host=host_addr,
                    port=port_num,
                    client_id=client_id_num,
                    market_data_type=settings.ibkr.get_market_data_type_enum(),
                )

                # Attempt connection
                connection_info = await client.connect(
                    timeout=settings.ibkr.ibkr_connection_timeout
                )

                progress.update(task, completed=True)

                # Display success message
                console.print(
                    "✅ Successfully connected to Interactive Brokers", style="green"
                )

                # Show connection details in table
                table = Table(title="Connection Details")
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="green")

                table.add_row("Host", host_addr)
                table.add_row("Port", str(port_num))
                table.add_row("Client ID", str(client_id_num))
                table.add_row("Account ID", connection_info.get("account_id", "N/A"))
                table.add_row(
                    "Server Version", str(connection_info.get("server_version", "N/A"))
                )
                table.add_row(
                    "Connection Time", connection_info.get("connection_time", "N/A")
                )

                console.print(table)

                # Disconnect
                await client.disconnect()

                return True

            except (ConnectionError, TimeoutError) as e:
                progress.update(task, completed=True)
                console.print(f"❌ Connection failed: {e}", style="red")

                # Show troubleshooting hints
                console.print("\n[yellow]Troubleshooting:[/yellow]")
                console.print(
                    "  1. Ensure TWS or IB Gateway is running on the specified host/port"
                )
                console.print(
                    "  2. Check that API connections are enabled in TWS/Gateway"
                )
                console.print("  3. Verify the host and port are correct")
                console.print(
                    "  4. For paper trading, use port 7497 (TWS) or 4002 (Gateway)"
                )

                return False

            except Exception as e:
                progress.update(task, completed=True)
                console.print(f"❌ Connection failed: {e}", style="red")
                return False

    # Run async function
    result = asyncio.run(connect_async())

    if not result:
        raise click.ClickException("Connection failed")


@data.command("fetch")
@click.option(
    "--instruments",
    "-i",
    required=True,
    help="Comma-separated list of instruments (e.g., AAPL,MSFT,GOOGL)",
)
@click.option(
    "--start",
    "-s",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end",
    "-e",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--timeframe",
    "-t",
    default="DAILY",
    type=click.Choice(
        ["1-MINUTE", "5-MINUTE", "1-HOUR", "DAILY"], case_sensitive=False
    ),
    help="Bar timeframe",
)
@click.option(
    "--host",
    default=None,
    help="IBKR Gateway/TWS host (defaults to settings)",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="IBKR Gateway/TWS port (defaults to settings)",
)
def fetch(
    instruments: str,
    start: datetime,
    end: datetime,
    timeframe: str,
    host: Optional[str],
    port: Optional[int],
):
    """Fetch historical data from Interactive Brokers."""

    async def fetch_async():
        # Parse instruments
        instrument_list = [s.strip().upper() for s in instruments.split(",")]

        # Use settings or override
        host_addr = host or settings.ibkr.ibkr_host
        port_num = port or settings.ibkr.ibkr_port

        # Map timeframe to bar specification
        bar_spec_map = {
            "1-MINUTE": "1-MINUTE-LAST",
            "5-MINUTE": "5-MINUTE-LAST",
            "1-HOUR": "1-HOUR-LAST",
            "DAILY": "1-DAY-LAST",
        }
        bar_spec = bar_spec_map[timeframe.upper()]

        total_bars = 0

        try:
            # Create client and connect
            client = IBKRHistoricalClient(
                host=host_addr,
                port=port_num,
                client_id=settings.ibkr.ibkr_client_id,
                market_data_type=settings.ibkr.get_market_data_type_enum(),
            )
            await client.connect(timeout=settings.ibkr.ibkr_connection_timeout)

            # Create fetcher
            fetcher = HistoricalDataFetcher(client)

            # Fetch data for each instrument
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=False,
            ) as progress:
                for symbol in instrument_list:
                    task = progress.add_task(
                        f"Fetching {symbol} ({timeframe})...", total=None
                    )

                    # Create contract
                    contract = IBContract(
                        secType="STK",
                        symbol=symbol,
                        exchange="SMART",
                        primaryExchange="NASDAQ",
                    )

                    # Fetch bars
                    bars = await fetcher.fetch_bars(
                        contracts=[contract],
                        bar_specifications=[bar_spec],
                        start_date=start,
                        end_date=end,
                    )

                    progress.update(task, completed=True)

                    total_bars += len(bars)

                    console.print(
                        f"  ✅ {symbol}: {len(bars)} bars fetched", style="green"
                    )

            # Disconnect
            await client.disconnect()

            # Display summary
            console.print(
                f"\n✅ Successfully fetched {total_bars} bars for {len(instrument_list)} instruments",
                style="green bold",
            )

            # Show summary table
            table = Table(title="Fetch Summary")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Instruments", ", ".join(instrument_list))
            table.add_row("Start Date", start.strftime("%Y-%m-%d"))
            table.add_row("End Date", end.strftime("%Y-%m-%d"))
            table.add_row("Timeframe", timeframe)
            table.add_row("Total Bars", str(total_bars))
            table.add_row("Data Location", str(fetcher.catalog_path))

            console.print(table)

            return True

        except (ConnectionError, TimeoutError) as e:
            console.print(f"❌ Connection failed: {e}", style="red")
            return False

        except Exception as e:
            console.print(f"❌ Fetch failed: {e}", style="red")
            return False

    # Run async function
    result = asyncio.run(fetch_async())

    if not result:
        raise click.ClickException("Fetch failed")
