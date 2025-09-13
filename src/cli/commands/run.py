"""Run commands for NTrader CLI."""

from decimal import Decimal

import click
from rich.console import Console

from src.config import get_settings
from src.core.backtest_runner import MinimalBacktestRunner

console = Console()


@click.command()
@click.option(
    "--strategy",
    default="sma",
    type=click.Choice(["sma"]),
    help="Trading strategy to use (default: sma)"
)
@click.option(
    "--data",
    default="mock",
    type=click.Choice(["mock"]),
    help="Data source to use (default: mock)"
)
@click.option(
    "--fast-period",
    type=int,
    help="Fast SMA period (default: from config)"
)
@click.option(
    "--slow-period",
    type=int,
    help="Slow SMA period (default: from config)"
)
@click.option(
    "--trade-size",
    type=str,
    help="Trade size (default: from config)"
)
@click.option(
    "--bars",
    type=int,
    help="Number of data bars to generate (default: from config)"
)
def run_simple(
    strategy: str,
    data: str,
    fast_period: int,
    slow_period: int,
    trade_size: str,
    bars: int
) -> None:
    """Run a simple backtest with mock data.

    This command runs a basic SMA crossover backtest using synthetic data
    for demonstration purposes.
    """
    console.print("[green]Running simple SMA backtest...[/green]")

    # Initialize backtest runner
    runner = MinimalBacktestRunner()

    try:
        # Parse trade size if provided
        trade_size_decimal = None
        if trade_size:
            trade_size_decimal = Decimal(trade_size)

        # Run the backtest
        console.print("[yellow]Initializing backtest engine...[/yellow]")
        result = runner.run_sma_backtest(
            fast_period=fast_period,
            slow_period=slow_period,
            trade_size=trade_size_decimal,
            num_bars=bars
        )

        # Display results
        console.print("[green]✓ Backtest completed successfully![/green]")
        console.print("\n[bold]Results Summary:[/bold]")
        console.print(f"Total Return: [{'green' if result.total_return >= 0 else 'red'}]{result.total_return:,.2f}[/{'green' if result.total_return >= 0 else 'red'}]")
        console.print(f"Total Trades: {result.total_trades}")
        console.print(f"Win Rate: {result.win_rate:.1f}%")

        if result.total_trades > 0:
            console.print(f"Winning Trades: {result.winning_trades}")
            console.print(f"Losing Trades: {result.losing_trades}")
            if result.largest_win > 0:
                console.print(f"Largest Win: [green]{result.largest_win:,.2f}[/green]")
            if result.largest_loss < 0:
                console.print(f"Largest Loss: [red]{result.largest_loss:,.2f}[/red]")

        console.print(f"Final Balance: [{'green' if result.final_balance >= 0 else 'red'}]{result.final_balance:,.2f}[/{'green' if result.final_balance >= 0 else 'red'}]")

        # Show performance message
        if result.total_return > 0:
            console.print("\n[green]🎉 Strategy shows profit on mock data![/green]")
        else:
            console.print("\n[red]📉 Strategy shows loss on mock data.[/red]")

    except Exception as e:
        console.print(f"[red]❌ Backtest failed: {str(e)}[/red]")
        raise click.ClickException(f"Backtest execution failed: {e}")

    finally:
        # Clean up resources
        runner.dispose()