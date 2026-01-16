"""Unit tests for CompositeSignalGenerator.

Tests cover:
- AND logic (all conditions must pass)
- OR logic (at least one must pass)
- Signal strength calculation
- Blocking component identification
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.signals.composite import CombinationLogic, CompositeSignalGenerator
from src.core.signals.evaluation import ComponentResult


@dataclass
class MockComponent:
    """Mock component for testing."""

    name: str
    triggered: bool = True
    value: float = 1.0
    reason: str = "Mock reason"

    def evaluate(self, bar: Any, cache: Any, **context: Any) -> ComponentResult:
        """Return a mock component result."""
        return ComponentResult(
            name=self.name,
            value=self.value,
            triggered=self.triggered,
            reason=self.reason,
        )


class MockBar:
    """Mock bar for testing."""

    ts_event: int = 1704067200000000000

    def __init__(self) -> None:
        self.close = 100.0


class TestCompositeSignalGeneratorAndLogic:
    """Tests for AND combination logic."""

    def test_and_all_pass_signal_true(self) -> None:
        """Test AND logic: all conditions pass -> signal=True."""
        components = [
            MockComponent("c1", triggered=True),
            MockComponent("c2", triggered=True),
            MockComponent("c3", triggered=True),
        ]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is True
        assert evaluation.strength == 1.0
        assert evaluation.blocking_component is None
        assert len(evaluation.components) == 3

    def test_and_one_fails_signal_false(self) -> None:
        """Test AND logic: one condition fails -> signal=False."""
        components = [
            MockComponent("c1", triggered=True),
            MockComponent("c2", triggered=False),  # This one fails
            MockComponent("c3", triggered=True),
        ]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is False
        assert evaluation.strength == pytest.approx(2 / 3, rel=0.01)
        assert evaluation.blocking_component == "c2"

    def test_and_all_fail_signal_false(self) -> None:
        """Test AND logic: all conditions fail -> signal=False."""
        components = [
            MockComponent("c1", triggered=False),
            MockComponent("c2", triggered=False),
            MockComponent("c3", triggered=False),
        ]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is False
        assert evaluation.strength == 0.0
        assert evaluation.blocking_component == "c1"  # First to fail

    def test_and_three_of_four_pass_is_near_miss(self) -> None:
        """Test 3 of 4 pass is near miss (75% strength)."""
        components = [
            MockComponent("c1", triggered=True),
            MockComponent("c2", triggered=True),
            MockComponent("c3", triggered=True),
            MockComponent("c4", triggered=False),
        ]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is False
        assert evaluation.strength == 0.75
        assert evaluation.is_near_miss is True
        assert evaluation.blocking_component == "c4"


class TestCompositeSignalGeneratorOrLogic:
    """Tests for OR combination logic."""

    def test_or_one_passes_signal_true(self) -> None:
        """Test OR logic: one condition passes -> signal=True."""
        components = [
            MockComponent("c1", triggered=False),
            MockComponent("c2", triggered=True),  # This one passes
            MockComponent("c3", triggered=False),
        ]

        generator = CompositeSignalGenerator(
            name="exit",
            components=components,
            logic=CombinationLogic.OR,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is True
        assert evaluation.strength == pytest.approx(1 / 3, rel=0.01)
        assert evaluation.blocking_component is None

    def test_or_all_pass_signal_true(self) -> None:
        """Test OR logic: all conditions pass -> signal=True."""
        components = [
            MockComponent("c1", triggered=True),
            MockComponent("c2", triggered=True),
        ]

        generator = CompositeSignalGenerator(
            name="exit",
            components=components,
            logic=CombinationLogic.OR,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is True
        assert evaluation.strength == 1.0

    def test_or_all_fail_signal_false(self) -> None:
        """Test OR logic: all conditions fail -> signal=False."""
        components = [
            MockComponent("c1", triggered=False),
            MockComponent("c2", triggered=False),
        ]

        generator = CompositeSignalGenerator(
            name="exit",
            components=components,
            logic=CombinationLogic.OR,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is False
        assert evaluation.strength == 0.0
        # For OR, the first failing component is still the blocker
        assert evaluation.blocking_component == "c1"


class TestCompositeSignalGeneratorStrength:
    """Tests for signal strength calculation."""

    def test_strength_calculation_various_ratios(self) -> None:
        """Test strength is calculated as passed/total."""
        test_cases = [
            (4, 4, 1.0),  # All pass
            (3, 4, 0.75),  # Near miss
            (2, 4, 0.5),  # Half pass
            (1, 4, 0.25),  # One pass
            (0, 4, 0.0),  # None pass
        ]

        for passed, total, expected_strength in test_cases:
            components = [MockComponent(f"c{i}", triggered=(i < passed)) for i in range(total)]

            generator = CompositeSignalGenerator(
                name="test",
                components=components,
                logic=CombinationLogic.AND,
            )

            bar = MockBar()
            cache = MagicMock()

            evaluation = generator.evaluate(bar, cache)

            assert evaluation.strength == pytest.approx(expected_strength, rel=0.01), (
                f"Expected strength {expected_strength} for {passed}/{total} passed"
            )


class TestCompositeSignalGeneratorBlocking:
    """Tests for blocking component identification."""

    def test_blocking_is_first_failing_component(self) -> None:
        """Test that blocking component is the first one to fail."""
        components = [
            MockComponent("c1", triggered=True),
            MockComponent("c2", triggered=False),  # First failure
            MockComponent("c3", triggered=False),  # Second failure
            MockComponent("c4", triggered=True),
        ]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.blocking_component == "c2"

    def test_no_blocking_when_signal_true(self) -> None:
        """Test that blocking_component is None when signal=True."""
        components = [
            MockComponent("c1", triggered=True),
            MockComponent("c2", triggered=True),
        ]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is True
        assert evaluation.blocking_component is None


class TestCompositeSignalGeneratorContext:
    """Tests for context passing to components."""

    def test_context_passed_to_components(self) -> None:
        """Test that context is passed to component evaluate()."""
        received_context = {}

        class ContextCapturingComponent:
            name = "context_test"

            def evaluate(self, bar: Any, cache: Any, **context: Any) -> ComponentResult:
                received_context.update(context)
                return ComponentResult(self.name, 1.0, True, "captured")

        generator = CompositeSignalGenerator(
            name="test",
            components=[ContextCapturingComponent()],
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        generator.evaluate(bar, cache, bars_held=5, prev_high=100.0)

        assert received_context["bars_held"] == 5
        assert received_context["prev_high"] == 100.0


class TestCompositeSignalGeneratorEdgeCases:
    """Tests for edge cases."""

    def test_single_component_and(self) -> None:
        """Test with single component using AND logic."""
        components = [MockComponent("c1", triggered=True)]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is True
        assert evaluation.strength == 1.0

    def test_single_component_or(self) -> None:
        """Test with single component using OR logic."""
        components = [MockComponent("c1", triggered=False)]

        generator = CompositeSignalGenerator(
            name="exit",
            components=components,
            logic=CombinationLogic.OR,
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.signal is False
        assert evaluation.strength == 0.0

    def test_evaluation_includes_bar_type(self) -> None:
        """Test that evaluation includes correct bar_type."""
        components = [MockComponent("c1", triggered=True)]

        generator = CompositeSignalGenerator(
            name="entry",
            components=components,
            logic=CombinationLogic.AND,
            bar_type="AAPL.XNAS-1-DAY-LAST",
        )

        bar = MockBar()
        cache = MagicMock()

        evaluation = generator.evaluate(bar, cache)

        assert evaluation.bar_type == "AAPL.XNAS-1-DAY-LAST"
