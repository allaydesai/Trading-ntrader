"""Shared test utilities and fixtures.

This module exports reusable test data and fixtures for use across all test categories.
Market scenarios provide consistent test data for integration testing.

Available Fixtures:
    - MarketScenario: Dataclass for defining market test scenarios
    - VOLATILE_MARKET: High volatility scenario for stress testing
    - TRENDING_MARKET: Steady uptrend scenario for positive case testing
    - RANGING_MARKET: Sideways movement scenario for range-bound testing
"""

from tests.fixtures.scenarios import (
    RANGING_MARKET,
    TRENDING_MARKET,
    VOLATILE_MARKET,
    MarketScenario,
)

__all__ = [
    "MarketScenario",
    "VOLATILE_MARKET",
    "TRENDING_MARKET",
    "RANGING_MARKET",
]
