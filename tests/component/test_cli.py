import pytest

"""Tests for CLI main module."""

from unittest.mock import Mock, patch

import click
from click.testing import CliRunner

from src.cli.main import cli


@pytest.mark.component
def test_cli_group_creation():
    """Test CLI group is created correctly."""
    assert isinstance(cli, click.Group)
    assert cli.name == "cli"  # CLI group has name "cli"


@pytest.mark.component
def test_cli_has_version_option():
    """Test CLI has version option configured."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "NTrader" in result.output
    assert "0.1.0" in result.output


@pytest.mark.component
def test_cli_context_initialization():
    """Test CLI context is properly initialized."""
    runner = CliRunner()

    @cli.command()
    @click.pass_context
    @pytest.mark.component
    def test_context(ctx):
        """Test command to check context."""
        click.echo(f"Context object type: {type(ctx.obj)}")

    result = runner.invoke(cli, ["test-context"])
    assert result.exit_code == 0
    assert "dict" in result.output

    # Clean up the test command
    if "test-context" in cli.commands:
        del cli.commands["test-context"]


@pytest.mark.component
def test_cli_help_message():
    """Test CLI help message contains expected information."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "NTrader - Nautilus Trader Backtesting System CLI" in result.output
    assert "production-grade algorithmic trading" in result.output
    assert "Interactive Brokers" in result.output


@pytest.mark.component
def test_run_simple_command_registered():
    """Test that run_simple command is registered with CLI."""
    assert "run-simple" in cli.commands
    command = cli.commands["run-simple"]
    assert isinstance(command, click.Command)


@pytest.mark.component
def test_cli_with_invalid_command():
    """Test CLI behavior with invalid command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["invalid-command"])

    assert result.exit_code == 2  # Click exits with 2 for usage errors
    assert "No such command" in result.output


@pytest.mark.component
def test_cli_settings_import():
    """Test that settings are imported and accessible."""
    with patch("src.cli.main.get_settings") as mock_get_settings:
        mock_settings = Mock()
        mock_settings.app_version = "test-version"
        mock_settings.app_name = "test-name"
        mock_get_settings.return_value = mock_settings

        # Reload the module to apply the patch
        import importlib
        from src.cli import main

        importlib.reload(main)

        assert main.settings is not None


@pytest.mark.component
def test_cli_help_long_option():
    """Test long help option works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output


@pytest.mark.component
def test_cli_console_initialization():
    """Test that Rich console is initialized."""
    from src.cli.main import console
    from rich.console import Console

    assert isinstance(console, Console)
