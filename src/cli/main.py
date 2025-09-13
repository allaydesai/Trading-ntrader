"""Main CLI application for NTrader."""

import click
from rich.console import Console

from src.config import get_settings
from src.cli.commands.run import run_simple

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


# Register the run_simple command
cli.add_command(run_simple)


if __name__ == "__main__":
    cli()