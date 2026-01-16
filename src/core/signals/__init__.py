"""Multi-condition signal validation module.

This module provides components for:
- Defining individual signal conditions (SignalComponent)
- Combining conditions with AND/OR logic (CompositeSignalGenerator)
- Capturing audit trail during backtests (SignalCollector)
- Post-backtest analysis (SignalAnalyzer)
"""

from src.core.signals.analysis import SignalAnalyzer, SignalStatistics
from src.core.signals.collector import SignalCollector
from src.core.signals.components import (
    FibonacciLevelComponent,
    PriceBreakoutComponent,
    RSIThresholdComponent,
    SignalComponent,
    TimeStopComponent,
    TrendFilterComponent,
    VolumeConfirmComponent,
)
from src.core.signals.composite import CombinationLogic, CompositeSignalGenerator
from src.core.signals.evaluation import ComponentResult, SignalEvaluation
from src.core.signals.integration import SignalValidationMixin

__all__ = [
    # Core data structures
    "ComponentResult",
    "SignalEvaluation",
    # Logic
    "CombinationLogic",
    "CompositeSignalGenerator",
    # Components
    "SignalComponent",
    "TrendFilterComponent",
    "RSIThresholdComponent",
    "VolumeConfirmComponent",
    "FibonacciLevelComponent",
    "PriceBreakoutComponent",
    "TimeStopComponent",
    # Collection
    "SignalCollector",
    # Analysis
    "SignalAnalyzer",
    "SignalStatistics",
    # Integration
    "SignalValidationMixin",
]
