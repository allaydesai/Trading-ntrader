"""Signal collector for audit trail during backtest.

This module contains:
- SignalCollector: Collects and stores signal evaluations during backtest execution
"""

import csv
from pathlib import Path

from src.core.signals.evaluation import SignalEvaluation


class SignalCollector:
    """Collects and stores signal evaluations during backtest execution.

    Provides memory-bounded collection with periodic flush to disk for
    long backtests, and exports audit trail to CSV format.

    Attributes:
        flush_threshold: Number of evaluations before flushing to disk
        output_dir: Directory for writing CSV chunks (None = no flush)
        evaluation_count: Total number of evaluations recorded

    Example:
        >>> collector = SignalCollector(flush_threshold=10000)
        >>> collector.record(evaluation)
        >>> collector.export_csv(Path("signals.csv"))
    """

    def __init__(
        self,
        flush_threshold: int = 10_000,
        output_dir: Path | None = None,
    ) -> None:
        """Initialize signal collector.

        Args:
            flush_threshold: Number of evaluations before flushing to disk
            output_dir: Directory for writing CSV chunks (required for flush)
        """
        self._evaluations: list[SignalEvaluation] = []
        self._flush_threshold = flush_threshold
        self._output_dir = output_dir
        self._chunk_count = 0
        self._total_count = 0
        self._component_names: set[str] = set()
        self._finalized = False

    @property
    def evaluations(self) -> list[SignalEvaluation]:
        """Get all evaluations currently in memory."""
        return self._evaluations

    @property
    def evaluation_count(self) -> int:
        """Get total number of evaluations recorded (including flushed)."""
        return self._total_count

    def record(self, evaluation: SignalEvaluation) -> None:
        """Record a signal evaluation.

        Args:
            evaluation: The signal evaluation to record

        Note:
            Will trigger flush to disk if threshold is reached and output_dir is set.
        """
        self._evaluations.append(evaluation)
        self._total_count += 1

        # Track component names for consistent CSV columns
        for component in evaluation.components:
            self._component_names.add(component.name)

        # Flush to disk if threshold reached
        if len(self._evaluations) >= self._flush_threshold:
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """Flush current evaluations to a chunk file."""
        if self._output_dir is None:
            return

        self._output_dir.mkdir(parents=True, exist_ok=True)
        chunk_file = self._output_dir / f"signals_{self._chunk_count:04d}.csv"
        self._write_csv(self._evaluations, chunk_file)
        self._evaluations.clear()
        self._chunk_count += 1

    def _get_csv_headers(self) -> list[str]:
        """Get CSV headers including flattened component columns."""
        base_headers = [
            "timestamp",
            "bar_type",
            "signal",
            "strength",
            "blocking_component",
            "signal_type",
            "order_id",
            "trade_id",
        ]

        # Add flattened component columns in sorted order
        component_headers = []
        for name in sorted(self._component_names):
            component_headers.extend(
                [
                    f"{name}_value",
                    f"{name}_triggered",
                    f"{name}_reason",
                ]
            )

        return base_headers + component_headers

    def _evaluation_to_row(self, evaluation: SignalEvaluation) -> dict[str, str]:
        """Convert evaluation to CSV row dictionary."""
        row: dict[str, str] = {
            "timestamp": str(evaluation.timestamp),
            "bar_type": evaluation.bar_type,
            "signal": str(evaluation.signal),
            "strength": str(evaluation.strength),
            "blocking_component": evaluation.blocking_component or "",
            "signal_type": evaluation.signal_type or "",
            "order_id": evaluation.order_id or "",
            "trade_id": evaluation.trade_id or "",
        }

        # Flatten component results
        for component in evaluation.components:
            row[f"{component.name}_value"] = str(component.value)
            row[f"{component.name}_triggered"] = str(component.triggered)
            row[f"{component.name}_reason"] = component.reason

        return row

    def _write_csv(
        self,
        evaluations: list[SignalEvaluation],
        output_path: Path,
        include_header: bool = True,
    ) -> None:
        """Write evaluations to CSV file.

        Args:
            evaluations: List of evaluations to write
            output_path: Path to output CSV file
            include_header: Whether to include header row
        """
        headers = self._get_csv_headers()

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")

            if include_header:
                writer.writeheader()

            for evaluation in evaluations:
                row = self._evaluation_to_row(evaluation)
                writer.writerow(row)

    def export_csv(self, output_path: Path) -> None:
        """Export all evaluations to a single CSV file.

        Args:
            output_path: Path to output CSV file

        Note:
            This exports only in-memory evaluations. For chunked data,
            use finalize() to merge all chunks.
        """
        self._write_csv(self._evaluations, output_path)

    def finalize(self, output_path: Path) -> None:
        """Finalize collection and merge all chunks into one file.

        Args:
            output_path: Path to final merged CSV file

        Note:
            This merges any flushed chunks with remaining in-memory evaluations.
        """
        if self._output_dir is None or self._chunk_count == 0:
            # No chunks, just export in-memory data
            self.export_csv(output_path)
            return

        # Flush any remaining data
        if self._evaluations:
            self._flush_to_disk()

        # Merge all chunks
        headers = self._get_csv_headers()

        with open(output_path, "w", newline="") as outf:
            writer = csv.DictWriter(outf, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()

            for chunk_idx in range(self._chunk_count):
                chunk_file = self._output_dir / f"signals_{chunk_idx:04d}.csv"
                if chunk_file.exists():
                    with open(chunk_file, newline="") as inf:
                        reader = csv.DictReader(inf)
                        for row in reader:
                            writer.writerow(row)

    def get_statistics(self) -> dict[str, float | int]:
        """Get basic statistics about recorded evaluations.

        Returns:
            Dictionary with total_evaluations, total_triggered, signal_rate
        """
        if self._total_count == 0:
            return {
                "total_evaluations": 0,
                "total_triggered": 0,
                "signal_rate": 0.0,
            }

        total_triggered = sum(1 for e in self._evaluations if e.signal)

        return {
            "total_evaluations": self._total_count,
            "total_triggered": total_triggered,
            "signal_rate": total_triggered / len(self._evaluations) if self._evaluations else 0.0,
        }

    def clear(self) -> None:
        """Clear all recorded evaluations."""
        self._evaluations.clear()
        self._total_count = 0
        self._chunk_count = 0
        self._component_names.clear()
