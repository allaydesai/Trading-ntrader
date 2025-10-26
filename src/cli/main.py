"""Main CLI application for NTrader."""

import click
from rich.console import Console

from src.config import get_settings
from src.cli.commands.run import run_simple
from src.cli.commands.data import data
from src.cli.commands.backtest import backtest
from src.cli.commands.strategy import strategy
from src.cli.commands.report import report
from src.cli.commands.history import list_backtest_history

console = Console()
settings = get_settings()


@click.group()
@click.version_option(version=settings.app_version, prog_name=settings.app_name)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """NTrader - Nautilus Trader Backtesting System CLI.

    A production-grade algorithmic trading backtesting system using
    Nautilus Trader framework with Interactive Brokers integration.
    """
    ctx.ensure_object(dict)


# Register commands
cli.add_command(run_simple)
cli.add_command(data)
cli.add_command(backtest)
cli.add_command(strategy)
cli.add_command(report)
cli.add_command(list_backtest_history)


if __name__ == "__main__":
    cli()
