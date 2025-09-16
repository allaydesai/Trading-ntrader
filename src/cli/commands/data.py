"""Data management commands."""

import asyncio
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.services.csv_loader import CSVLoader
from src.db.session import test_connection

console = Console()


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
