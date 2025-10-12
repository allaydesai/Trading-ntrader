"""Data management commands."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.services.csv_loader import CSVLoader
from src.db.session import test_connection
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


@data.command("import-csv")
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to CSV file to import",
)
@click.option("--symbol", "-s", required=True, help="Trading symbol (e.g., AAPL)")
def import_csv(file: Path, symbol: str):
    """Import CSV market data to database."""

    async def import_csv_async():
        # Check database connection first
        if not await test_connection():
            console.print(
                "❌ Database not accessible. Please check your database configuration.",
                style="red",
            )
            return False

        # Show progress while importing
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task(f"Importing {file.name}...", total=None)

            try:
                # Load CSV file
                loader = CSVLoader()
                result = await loader.load_file(file, symbol.upper())

                progress.update(task, completed=True)

                # Display results
                console.print(
                    f"✅ Successfully imported {result['records_inserted']} records",
                    style="green",
                )

                if result["duplicates_skipped"] > 0:
                    console.print(
                        f"⚠️  Skipped {result['duplicates_skipped']} duplicate records",
                        style="yellow",
                    )

                # Show summary table
                table = Table(title="Import Summary")
                table.add_column("Metric", style="cyan")
                table.add_column("Value", style="green")

                table.add_row("File", str(file))
                table.add_row("Symbol", result["symbol"])
                table.add_row("Records Processed", str(result["records_processed"]))
                table.add_row("Records Inserted", str(result["records_inserted"]))
                table.add_row("Duplicates Skipped", str(result["duplicates_skipped"]))

                console.print(table)
                return True

            except FileNotFoundError:
                progress.update(task, completed=True)
                console.print(f"❌ File not found: {file}", style="red")
                return False
            except ValueError as e:
                progress.update(task, completed=True)
                console.print(f"❌ Invalid CSV format: {e}", style="red")
                return False
            except Exception as e:
                progress.update(task, completed=True)
                console.print(f"❌ Import failed: {e}", style="red")
                return False

    # Run async function
    result = asyncio.run(import_csv_async())

    if not result:
        raise click.ClickException("Import failed")


@data.command("list")
@click.option("--symbol", "-s", help="Filter by trading symbol")
def list_data(symbol: Optional[str]):
    """List available market data."""
    # This will be implemented later when we have the data service
    console.print("📊 Data listing feature coming soon...", style="yellow")

    if symbol:
        console.print(f"   Filtering by symbol: {symbol.upper()}")


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
