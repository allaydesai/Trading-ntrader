"""Unit tests for SignalCollector.

Tests cover:
- Recording signal evaluations
- CSV export functionality
- Memory-bounded flush mechanism
- Statistics calculation
"""

import csv
import tempfile
from pathlib import Path

import pytest

from src.core.signals.collector import SignalCollector
from src.core.signals.evaluation import ComponentResult, SignalEvaluation


@pytest.fixture
def sample_evaluations() -> list[SignalEvaluation]:
    """Provide sample signal evaluations for testing."""
    return [
        SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 152.3, True, "Close > SMA"),
                ComponentResult("rsi", 8.5, True, "RSI < 10"),
            ],
            signal=True,
            strength=1.0,
        ),
        SignalEvaluation(
            timestamp=1704153600000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 148.0, False, "Close < SMA"),
                ComponentResult("rsi", 12.0, False, "RSI >= 10"),
            ],
            signal=False,
            strength=0.0,
            blocking_component="trend",
        ),
        SignalEvaluation(
            timestamp=1704240000000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 155.0, True, "Close > SMA"),
                ComponentResult("rsi", 15.0, False, "RSI >= 10"),
            ],
            signal=False,
            strength=0.5,
            blocking_component="rsi",
        ),
    ]


class TestSignalCollectorRecording:
    """Tests for recording signal evaluations."""

    def test_record_single_evaluation(self) -> None:
        """Test recording a single evaluation."""
        collector = SignalCollector()

        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[ComponentResult("c1", 1.0, True, "passed")],
            signal=True,
            strength=1.0,
        )

        collector.record(evaluation)

        assert len(collector.evaluations) == 1
        assert collector.evaluations[0] is evaluation

    def test_record_multiple_evaluations(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test recording multiple evaluations."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        assert len(collector.evaluations) == 3

    def test_evaluation_count_property(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test evaluation_count property."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        assert collector.evaluation_count == 3


class TestSignalCollectorExport:
    """Tests for CSV export functionality."""

    def test_export_csv_creates_file(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test that export_csv creates a file."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "signals.csv"
            collector.export_csv(output_path)

            assert output_path.exists()

    def test_export_csv_correct_row_count(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test that exported CSV has correct number of rows."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "signals.csv"
            collector.export_csv(output_path)

            with open(output_path, newline="") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Header + 3 data rows
            assert len(rows) == 4

    def test_export_csv_contains_required_columns(
        self, sample_evaluations: list[SignalEvaluation]
    ) -> None:
        """Test that exported CSV contains required columns."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "signals.csv"
            collector.export_csv(output_path)

            with open(output_path, newline="") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames

            required_columns = [
                "timestamp",
                "bar_type",
                "signal",
                "strength",
                "blocking_component",
            ]

            for col in required_columns:
                assert col in headers, f"Missing column: {col}"

    def test_export_csv_flattened_components(
        self, sample_evaluations: list[SignalEvaluation]
    ) -> None:
        """Test that component values are flattened in CSV."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "signals.csv"
            collector.export_csv(output_path)

            with open(output_path, newline="") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames

            # Check for flattened component columns
            assert "trend_value" in headers
            assert "trend_triggered" in headers
            assert "rsi_value" in headers
            assert "rsi_triggered" in headers

    def test_export_csv_data_values(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test that exported data values are correct."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "signals.csv"
            collector.export_csv(output_path)

            with open(output_path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            # First row: signal=True
            assert rows[0]["signal"] == "True"
            assert rows[0]["strength"] == "1.0"

            # Second row: signal=False, blocked by trend
            assert rows[1]["signal"] == "False"
            assert rows[1]["blocking_component"] == "trend"


class TestSignalCollectorFlush:
    """Tests for memory-bounded flush mechanism."""

    def test_flush_threshold_triggers_write(self) -> None:
        """Test that flush is triggered when threshold is reached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            collector = SignalCollector(
                flush_threshold=5,
                output_dir=output_dir,
            )

            # Record 5 evaluations (at threshold)
            for i in range(5):
                evaluation = SignalEvaluation(
                    timestamp=1704067200000000000 + i,
                    bar_type="AAPL.XNAS-1-DAY-LAST",
                    components=[ComponentResult("c1", 1.0, True, "r")],
                    signal=True,
                    strength=1.0,
                )
                collector.record(evaluation)

            # In-memory list should be cleared after flush
            assert len(collector._evaluations) == 0
            assert collector._chunk_count == 1

            # Check that chunk file was created
            chunk_files = list(output_dir.glob("signals_*.csv"))
            assert len(chunk_files) == 1

    def test_no_flush_below_threshold(self) -> None:
        """Test that no flush occurs below threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            collector = SignalCollector(
                flush_threshold=10,
                output_dir=output_dir,
            )

            # Record 5 evaluations (below threshold of 10)
            for i in range(5):
                evaluation = SignalEvaluation(
                    timestamp=1704067200000000000 + i,
                    bar_type="AAPL.XNAS-1-DAY-LAST",
                    components=[ComponentResult("c1", 1.0, True, "r")],
                    signal=True,
                    strength=1.0,
                )
                collector.record(evaluation)

            # Should still be in memory
            assert len(collector._evaluations) == 5
            assert collector._chunk_count == 0

    def test_multiple_flush_chunks(self) -> None:
        """Test that multiple chunks are created for large datasets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            collector = SignalCollector(
                flush_threshold=5,
                output_dir=output_dir,
            )

            # Record 12 evaluations -> 2 chunks + 2 in memory
            for i in range(12):
                evaluation = SignalEvaluation(
                    timestamp=1704067200000000000 + i,
                    bar_type="AAPL.XNAS-1-DAY-LAST",
                    components=[ComponentResult("c1", 1.0, True, "r")],
                    signal=True,
                    strength=1.0,
                )
                collector.record(evaluation)

            assert collector._chunk_count == 2
            assert len(collector._evaluations) == 2

    def test_finalize_merges_chunks(self) -> None:
        """Test that finalize() merges all chunks into one file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            collector = SignalCollector(
                flush_threshold=5,
                output_dir=output_dir,
            )

            # Record 12 evaluations
            for i in range(12):
                evaluation = SignalEvaluation(
                    timestamp=1704067200000000000 + i,
                    bar_type="AAPL.XNAS-1-DAY-LAST",
                    components=[ComponentResult("c1", 1.0, True, "r")],
                    signal=True,
                    strength=1.0,
                )
                collector.record(evaluation)

            # Finalize to merge
            final_path = output_dir / "final_signals.csv"
            collector.finalize(final_path)

            with open(final_path, newline="") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Header + 12 data rows
            assert len(rows) == 13


class TestSignalCollectorStatistics:
    """Tests for basic statistics calculation."""

    def test_get_basic_statistics(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test basic statistics from collector."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        stats = collector.get_statistics()

        assert stats["total_evaluations"] == 3
        assert stats["total_triggered"] == 1  # Only first one has signal=True
        assert stats["signal_rate"] == pytest.approx(1 / 3, rel=0.01)

    def test_empty_collector_statistics(self) -> None:
        """Test statistics on empty collector."""
        collector = SignalCollector()

        stats = collector.get_statistics()

        assert stats["total_evaluations"] == 0
        assert stats["total_triggered"] == 0
        assert stats["signal_rate"] == 0.0


class TestSignalCollectorClear:
    """Tests for clearing collector state."""

    def test_clear_removes_evaluations(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test that clear removes all evaluations."""
        collector = SignalCollector()

        for evaluation in sample_evaluations:
            collector.record(evaluation)

        assert len(collector.evaluations) == 3

        collector.clear()

        assert len(collector.evaluations) == 0
        assert collector.evaluation_count == 0
