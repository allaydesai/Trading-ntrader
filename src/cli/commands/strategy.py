"""Strategy management commands."""

import click
import yaml
from rich.console import Console
from rich.table import Table

from src.core.strategy_factory import StrategyLoader
from src.core.strategy_registry import StrategyRegistry
from src.utils.config_loader import ConfigLoader

console = Console(width=200)


def validate_strategy_type(ctx, param, value):
    """Validate strategy type against registry."""
    StrategyRegistry.discover()
    if not StrategyRegistry.exists(value):
        available = StrategyRegistry.get_names()
        raise click.BadParameter(
            f"Unknown strategy '{value}'. Available strategies: {', '.join(available)}"
        )
    return value


@click.group()
def strategy():
    """Strategy management commands."""
    pass


@strategy.command()
def list():
    """List available strategies."""
    console.print("📊 Available Strategies", style="cyan bold")
    console.print()

    strategies = StrategyLoader.list_available()

    # Create strategies table
    table = Table(title="Supported Trading Strategies")
    table.add_column("Strategy Type", style="cyan", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Implementation", style="yellow")

    for strategy_name, info in strategies.items():
        # Extract class name from strategy path
        class_name = info["strategy_path"].split(":")[-1]
        table.add_row(strategy_name, info["description"], class_name)

    console.print(table)
    console.print()

    # Show usage example
    console.print("💡 Usage Examples", style="green bold")
    console.print(
        "   Create config template: ntrader strategy create "
        "--type sma_crossover --output my_config.yaml"
    )
    console.print("   Validate config:        ntrader strategy validate my_config.yaml")
    console.print("   Run backtest:           ntrader backtest run-config my_config.yaml")


@strategy.command()
@click.option(
    "--type",
    "strategy_type",
    required=True,
    callback=validate_strategy_type,
    help="Strategy type to create template for. Use 'strategy list' for available types.",
)
@click.option(
    "--output",
    "output_file",
    required=True,
    type=click.Path(),
    help="Output file path for the YAML template",
)
def create(strategy_type: str, output_file: str):
    """Create strategy config template."""
    try:
        console.print(f"🔧 Creating {strategy_type} config template...", style="cyan")

        # Generate template using StrategyLoader
        template = StrategyLoader.create_template(strategy_type)

        # Write YAML template to file
        with open(output_file, "w") as f:
            yaml.dump(template, f, default_flow_style=False, indent=2)

        console.print(f"✅ Created {output_file}", style="green bold")
        console.print(f"   Strategy: {strategy_type}")
        console.print(
            f"   Template includes default parameters for {template['config']['instrument_id']}"
        )
        console.print()
        console.print("💡 Next steps:")
        console.print(f"   1. Edit {output_file} to customize parameters")
        console.print(f"   2. Validate: ntrader strategy validate {output_file}")
        console.print(f"   3. Run: ntrader backtest run-config {output_file}")

    except ValueError as e:
        console.print(f"❌ Error creating template: {e}", style="red")
        raise click.ClickException(str(e))
    except Exception as e:
        console.print(f"❌ Unexpected error: {e}", style="red")
        raise click.ClickException(f"Failed to create template: {e}")


@strategy.command()
@click.argument("config_file", type=click.Path())
def validate(config_file: str):
    """Validate strategy config file."""
    try:
        console.print(f"🔍 Validating {config_file}...", style="cyan")

        # Load and validate configuration
        config_obj = ConfigLoader.load_from_file(config_file)

        # If we get here, the config is valid
        console.print("✅ Config valid", style="green bold")
        console.print(f"   Strategy: {config_obj.strategy_path.split(':')[-1]}")
        console.print(f"   Config class: {config_obj.config_path.split(':')[-1]}")

        # Show key configuration parameters
        config_dict = {}
        for attr_name in dir(config_obj.config):
            if not attr_name.startswith("_") and not callable(
                getattr(config_obj.config, attr_name)
            ):
                try:
                    config_dict[attr_name] = getattr(config_obj.config, attr_name)
                except AttributeError:
                    continue

        if config_dict:
            console.print()
            console.print("📋 Configuration Parameters:", style="blue")
            for key, value in config_dict.items():
                console.print(f"   {key}: {value}")

    except FileNotFoundError:
        console.print(f"❌ Config invalid: File not found: {config_file}", style="red")
    except Exception as e:
        console.print(f"❌ Config invalid: {e}", style="red")
