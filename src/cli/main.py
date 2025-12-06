"""Main CLI application for NTrader."""

import click
from rich.console import Console

from src.cli.commands.backtest import backtest  # noqa: E402
from src.cli.commands.data import data  # noqa: E402
from src.cli.commands.history import list_backtest_history  # noqa: E402
from src.cli.commands.report import report  # noqa: E402
from src.cli.commands.run import run_simple  # noqa: E402
from src.cli.commands.strategy import strategy  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402

console = Console()
settings = get_settings()

# Configure logging on startup
configure_logging()


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
