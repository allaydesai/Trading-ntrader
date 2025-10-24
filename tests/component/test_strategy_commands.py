"""Tests for Strategy CLI commands."""

import os
import tempfile
from unittest.mock import Mock, patch

import pytest
import yaml
from click.testing import CliRunner

from src.cli.main import cli


class TestStrategyCommands:
    """Test cases for strategy CLI commands."""

    def setup_method(self):
        """Set up test runner."""
        self.runner = CliRunner()

    @pytest.mark.integration
    @pytest.mark.component
    def test_strategy_list_command(self):
        """INTEGRATION: List available strategies."""
        result = self.runner.invoke(cli, ["strategy", "list"])

        assert result.exit_code == 0
        assert "sma_crossover" in result.output
        assert "mean_reversion" in result.output
        assert "momentum" in result.output

        # Should show strategy descriptions
        assert "Simple Moving Average Crossover" in result.output
        assert "Mean Reversion Strategy" in result.output
        assert "Momentum Strategy" in result.output

    @pytest.mark.integration
    @pytest.mark.component
    def test_strategy_create_command_sma(self):
        """INTEGRATION: Create SMA config template."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "test_sma.yaml")

            result = self.runner.invoke(
                cli,
                [
                    "strategy",
                    "create",
                    "--type",
                    "sma_crossover",
                    "--output",
                    output_file,
                ],
            )

            assert result.exit_code == 0
            assert f"Created {output_file}" in result.output
            assert os.path.exists(output_file)

            # Validate generated YAML loads correctly
            with open(output_file, "r") as f:
                config = yaml.safe_load(f)

            assert (
                config["strategy_path"]
                == "src.core.strategies.sma_crossover:SMACrossover"
            )
            assert (
                config["config_path"] == "src.core.strategies.sma_crossover:SMAConfig"
            )
            assert "config" in config
            assert "fast_period" in config["config"]
            assert "slow_period" in config["config"]
            assert "trade_size" in config["config"]

    @pytest.mark.integration
    @pytest.mark.component
    def test_strategy_create_command_mean_reversion(self):
        """INTEGRATION: Create Mean Reversion config template."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "test_mr.yaml")

            result = self.runner.invoke(
                cli,
                [
                    "strategy",
                    "create",
                    "--type",
                    "mean_reversion",
                    "--output",
                    output_file,
                ],
            )

            assert result.exit_code == 0
            assert f"Created {output_file}" in result.output
            assert os.path.exists(output_file)

            # Validate generated YAML loads correctly
            with open(output_file, "r") as f:
                config = yaml.safe_load(f)

            assert (
                config["strategy_path"]
                == "src.core.strategies.rsi_mean_reversion:RSIMeanRev"
            )
            assert (
                config["config_path"]
                == "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig"
            )
            assert "rsi_period" in config["config"]
            assert "rsi_buy_threshold" in config["config"]

    @pytest.mark.integration
    @pytest.mark.component
    def test_strategy_create_command_momentum(self):
        """INTEGRATION: Create Momentum config template."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "test_momentum.yaml")

            result = self.runner.invoke(
                cli,
                ["strategy", "create", "--type", "momentum", "--output", output_file],
            )

            assert result.exit_code == 0
            assert f"Created {output_file}" in result.output
            assert os.path.exists(output_file)

            # Validate generated YAML loads correctly
            with open(output_file, "r") as f:
                config = yaml.safe_load(f)

            assert (
                config["strategy_path"]
                == "src.core.strategies.sma_momentum:SMAMomentum"
            )
            assert (
                config["config_path"]
                == "src.core.strategies.sma_momentum:SMAMomentumConfig"
            )
            assert "fast_period" in config["config"]
            assert "slow_period" in config["config"]
            assert "allow_short" in config["config"]

    @pytest.mark.component
    def test_strategy_create_command_invalid_type(self):
        """Test strategy create with invalid strategy type."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "test_invalid.yaml")

            result = self.runner.invoke(
                cli,
                [
                    "strategy",
                    "create",
                    "--type",
                    "invalid_strategy",
                    "--output",
                    output_file,
                ],
            )

            assert result.exit_code != 0
            assert (
                "Invalid strategy type" in result.output
                or "invalid choice" in result.output
                or "not one of" in result.output
            )
            assert not os.path.exists(output_file)

    @pytest.mark.integration
    @pytest.mark.component
    def test_strategy_validate_command_valid_config(self):
        """INTEGRATION: Validate a valid strategy config file."""
        # First create a valid config file
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = os.path.join(temp_dir, "valid_config.yaml")

            # Create config using our create command
            create_result = self.runner.invoke(
                cli,
                [
                    "strategy",
                    "create",
                    "--type",
                    "sma_crossover",
                    "--output",
                    config_file,
                ],
            )
            assert create_result.exit_code == 0

            # Now validate it
            result = self.runner.invoke(cli, ["strategy", "validate", config_file])

            assert result.exit_code == 0
            assert "✅ Config valid" in result.output

    @pytest.mark.component
    def test_strategy_validate_command_invalid_config(self):
        """Test strategy validate with invalid config file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = os.path.join(temp_dir, "invalid_config.yaml")

            # Create invalid YAML file
            with open(config_file, "w") as f:
                f.write("""
                strategy_path: "invalid.path"
                config:
                  invalid_field: "value"
                """)

            result = self.runner.invoke(cli, ["strategy", "validate", config_file])

            assert result.exit_code == 0  # Command runs, but shows error
            assert "❌ Config invalid" in result.output

    @pytest.mark.component
    def test_strategy_validate_command_missing_file(self):
        """Test strategy validate with non-existent file."""
        result = self.runner.invoke(cli, ["strategy", "validate", "nonexistent.yaml"])

        assert result.exit_code == 0  # Command runs, but shows error
        assert "❌ Config invalid" in result.output

    @pytest.mark.component
    def test_strategy_create_missing_output(self):
        """Test strategy create without output parameter."""
        result = self.runner.invoke(
            cli, ["strategy", "create", "--type", "sma_crossover"]
        )

        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output

    @pytest.mark.component
    def test_strategy_create_missing_type(self):
        """Test strategy create without type parameter."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "test.yaml")

            result = self.runner.invoke(
                cli, ["strategy", "create", "--output", output_file]
            )

            assert result.exit_code != 0
            assert "Missing option" in result.output or "required" in result.output

    @pytest.mark.component
    def test_strategy_group_help(self):
        """Test strategy command group help."""
        result = self.runner.invoke(cli, ["strategy", "--help"])

        assert result.exit_code == 0
        assert "Strategy management commands" in result.output
        assert "list" in result.output
        assert "create" in result.output
        assert "validate" in result.output

    @pytest.mark.component
    def test_strategy_list_help(self):
        """Test strategy list command help."""
        result = self.runner.invoke(cli, ["strategy", "list", "--help"])

        assert result.exit_code == 0
        assert "List available strategies" in result.output

    @pytest.mark.component
    def test_strategy_create_help(self):
        """Test strategy create command help."""
        result = self.runner.invoke(cli, ["strategy", "create", "--help"])

        assert result.exit_code == 0
        assert "Create strategy config template" in result.output
        assert "--type" in result.output
        assert "--output" in result.output

    @pytest.mark.component
    def test_strategy_validate_help(self):
        """Test strategy validate command help."""
        result = self.runner.invoke(cli, ["strategy", "validate", "--help"])

        assert result.exit_code == 0
        assert "Validate strategy config file" in result.output

    @pytest.mark.integration
    @pytest.mark.component
    def test_strategy_commands_preserve_existing_functionality(self):
        """INTEGRATION: Ensure existing CLI commands still work after adding strategy commands."""
        # Test that run-simple still works
        with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
            mock_runner = Mock()
            mock_result = Mock()
            mock_result.total_return = 1000.0
            mock_result.total_trades = 10
            mock_result.win_rate = 60.0
            mock_result.winning_trades = 6
            mock_result.losing_trades = 4
            mock_result.largest_win = 500.0
            mock_result.largest_loss = -300.0
            mock_result.final_balance = 101000.0

            mock_runner.run_sma_backtest.return_value = mock_result
            mock_runner_class.return_value = mock_runner

            result = self.runner.invoke(cli, ["run-simple"])
            assert result.exit_code == 0
            assert "Running simple SMA backtest" in result.output

        # Test that help still shows all commands
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "run-simple" in result.output
        assert "backtest" in result.output
        assert "data" in result.output
        assert "strategy" in result.output  # New command should be present
