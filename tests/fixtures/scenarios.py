"""Market scenario test data for integration tests.

This module provides predefined market scenarios (price sequences) for testing
trading strategies under different market conditions.

Purpose: Reusable test data for integration tests with real Nautilus components
Reference: design.md Section 3.1 - Integration Testing with Market Scenarios
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class MarketScenario:
    """Immutable market scenario with price sequence and metadata.

    Attributes:
        name: Descriptive name of the market scenario
        description: Brief description of market conditions
        prices: Sequence of prices representing the market movement
        expected_trades: Expected number of trades in this scenario
        expected_pnl_positive: Whether this scenario should produce positive PnL
    """

    name: str
    description: str
    prices: tuple[Decimal, ...]  # Immutable sequence
    expected_trades: int
    expected_pnl_positive: bool


# VOLATILE_MARKET Scenario (T029)
# High volatility with frequent price swings - tests strategy under rapid changes
VOLATILE_MARKET = MarketScenario(
    name="Volatile Market",
    description="High volatility with frequent price swings and reversals",
    prices=(
        Decimal("50000"),
        Decimal("51000"),
        Decimal("49500"),
        Decimal("52000"),
        Decimal("48000"),
        Decimal("53000"),
        Decimal("47500"),
        Decimal("54000"),
        Decimal("46000"),
        Decimal("55000"),
        Decimal("45000"),
        Decimal("56000"),
        Decimal("44000"),
        Decimal("57000"),
        Decimal("43000"),
        Decimal("58000"),
    ),
    expected_trades=8,  # Frequent crossovers in volatile conditions
    expected_pnl_positive=False,  # Choppy markets typically hurt trend-following
)

# TRENDING_MARKET Scenario (T030)
# Steady uptrend - ideal conditions for trend-following strategies like SMA
TRENDING_MARKET = MarketScenario(
    name="Trending Market",
    description="Steady uptrend with minimal pullbacks - ideal for trend-following",
    prices=(
        Decimal("50000"),
        Decimal("50500"),
        Decimal("51000"),
        Decimal("51500"),
        Decimal("52000"),
        Decimal("52500"),
        Decimal("53000"),
        Decimal("53500"),
        Decimal("54000"),
        Decimal("54500"),
        Decimal("55000"),
        Decimal("55500"),
        Decimal("56000"),
        Decimal("56500"),
        Decimal("57000"),
        Decimal("57500"),
    ),
    expected_trades=2,  # One golden cross entry, one death cross exit
    expected_pnl_positive=True,  # Trending markets are profitable for SMA strategies
)

# RANGING_MARKET Scenario (T031)
# Sideways movement within a range - tests strategy in non-trending conditions
RANGING_MARKET = MarketScenario(
    name="Ranging Market",
    description="Sideways movement within a price range - no clear trend",
    prices=(
        Decimal("50000"),
        Decimal("50500"),
        Decimal("50200"),
        Decimal("50700"),
        Decimal("50300"),
        Decimal("50600"),
        Decimal("50400"),
        Decimal("50500"),
        Decimal("50300"),
        Decimal("50600"),
        Decimal("50200"),
        Decimal("50700"),
        Decimal("50400"),
        Decimal("50500"),
        Decimal("50300"),
        Decimal("50400"),
    ),
    expected_trades=4,  # Multiple small crossovers in ranging market
    expected_pnl_positive=False,  # Ranging markets typically produce losses from whipsaws
)
