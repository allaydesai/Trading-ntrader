"""Simple JSON-based results persistence for backtest results."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.backtest_result import BacktestResult


class ResultsStoreError(Exception):
    """Base exception for ResultsStore errors."""

    pass


class ResultNotFoundError(ResultsStoreError):
    """Raised when a result ID is not found."""

    pass


class ResultsStore:
    """
    Simple file-based persistence for backtest results.

    Results are stored as JSON files in the .ntrader/results directory.
    Each result is saved with its unique ID as the filename.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        """
        Initialize the results store.

        Args:
            storage_dir: Directory to store results. Defaults to .ntrader/results
        """
        if storage_dir is None:
            storage_dir = Path.home() / ".ntrader" / "results"

        self.storage_dir = Path(storage_dir)
        self._ensure_storage_dir()

    def _ensure_storage_dir(self) -> None:
        """Ensure storage directory exists."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Create .gitignore to exclude results from git
        gitignore_path = self.storage_dir / ".gitignore"
        if not gitignore_path.exists():
            with open(gitignore_path, "w") as f:
                f.write("# Ignore all backtest result files\n*.json\n")

    def _get_result_path(self, result_id: str) -> Path:
        """
        Get file path for a result ID.

        Args:
            result_id: Unique backtest result ID

        Returns:
            Path to result file
        """
        return self.storage_dir / f"{result_id}.json"

    def save(self, result: BacktestResult) -> str:
        """
        Save backtest result to storage.

        Args:
            result: BacktestResult to save

        Returns:
            Result ID

        Raises:
            ResultsStoreError: If save fails
        """
        try:
            result_id = result.result_id
            file_path = self._get_result_path(result_id)

            # Convert to dictionary for JSON serialization
            result_data = result.to_dict()

            # Write to JSON file with pretty printing
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2, ensure_ascii=False, default=str)

            return result_id

        except Exception as e:
            raise ResultsStoreError(f"Failed to save result: {e}") from e

    def get(self, result_id: str) -> BacktestResult:
        """
        Load backtest result from storage.

        Args:
            result_id: Unique backtest result ID

        Returns:
            BacktestResult instance

        Raises:
            ResultNotFoundError: If result ID not found
            ResultsStoreError: If load fails
        """
        file_path = self._get_result_path(result_id)

        if not file_path.exists():
            raise ResultNotFoundError(f"Result not found: {result_id}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                result_data = json.load(f)

            return BacktestResult(**result_data)

        except json.JSONDecodeError as e:
            raise ResultsStoreError(f"Failed to parse result file: {e}") from e
        except Exception as e:
            raise ResultsStoreError(f"Failed to load result: {e}") from e

    def list(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List all available backtest results with metadata.

        Args:
            limit: Optional limit on number of results to return

        Returns:
            List of result metadata dictionaries, sorted by timestamp (most recent first)
        """
        results = []

        # Get all JSON files in storage directory
        json_files = list(self.storage_dir.glob("*.json"))

        for file_path in json_files:
            if file_path.name == ".gitignore":
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    result_data = json.load(f)

                # Extract metadata for listing
                metadata = result_data.get("metadata", {})
                summary = result_data.get("summary", {})

                results.append(
                    {
                        "result_id": metadata.get("backtest_id", file_path.stem),
                        "timestamp": metadata.get("timestamp"),
                        "strategy": metadata.get("strategy_name", "Unknown"),
                        "symbol": metadata.get("symbol", "Unknown"),
                        "start_date": metadata.get("start_date"),
                        "end_date": metadata.get("end_date"),
                        "total_return": summary.get("total_return", "0"),
                        "total_trades": summary.get("total_trades", 0),
                        "win_rate": summary.get("win_rate", 0.0),
                        "sharpe_ratio": summary.get("sharpe_ratio"),
                        "file_path": str(file_path),
                    }
                )

            except Exception:
                # Skip corrupted files
                continue

        # Sort by timestamp from metadata (most recent first)
        # Handle potential None timestamps by treating them as very old dates
        results.sort(key=lambda r: r.get("timestamp") or "1970-01-01 00:00:00", reverse=True)

        # Apply limit after sorting
        if limit:
            results = results[:limit]

        return results

    def delete(self, result_id: str) -> bool:
        """
        Delete a backtest result from storage.

        Args:
            result_id: Unique backtest result ID

        Returns:
            True if deleted successfully

        Raises:
            ResultNotFoundError: If result ID not found
        """
        file_path = self._get_result_path(result_id)

        if not file_path.exists():
            raise ResultNotFoundError(f"Result not found: {result_id}")

        try:
            file_path.unlink()
            return True
        except Exception as e:
            raise ResultsStoreError(f"Failed to delete result: {e}") from e

    def exists(self, result_id: str) -> bool:
        """
        Check if a result exists in storage.

        Args:
            result_id: Unique backtest result ID

        Returns:
            True if result exists, False otherwise
        """
        file_path = self._get_result_path(result_id)
        return file_path.exists()

    def count(self) -> int:
        """
        Get count of stored results.

        Returns:
            Number of results in storage
        """
        json_files = [f for f in self.storage_dir.glob("*.json") if f.name != ".gitignore"]
        return len(json_files)

    def clear(self) -> int:
        """
        Delete all stored results.

        Returns:
            Number of results deleted

        Warning:
            This operation cannot be undone!
        """
        count = 0
        json_files = [f for f in self.storage_dir.glob("*.json") if f.name != ".gitignore"]

        for file_path in json_files:
            try:
                file_path.unlink()
                count += 1
            except Exception:
                continue

        return count

    def get_storage_info(self) -> Dict[str, Any]:
        """
        Get information about the storage.

        Returns:
            Dictionary with storage statistics
        """
        json_files = [f for f in self.storage_dir.glob("*.json") if f.name != ".gitignore"]

        total_size = sum(f.stat().st_size for f in json_files)

        return {
            "storage_dir": str(self.storage_dir),
            "result_count": len(json_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "exists": self.storage_dir.exists(),
        }

    def find_by_strategy(self, strategy_name: str) -> List[Dict[str, Any]]:
        """
        Find results by strategy name.

        Args:
            strategy_name: Strategy name to search for

        Returns:
            List of matching result metadata
        """
        all_results = self.list()
        return [r for r in all_results if r["strategy"].lower() == strategy_name.lower()]

    def find_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Find results by trading symbol.

        Args:
            symbol: Symbol to search for

        Returns:
            List of matching result metadata
        """
        all_results = self.list()
        return [r for r in all_results if r["symbol"].lower() == symbol.lower()]

    def get_latest(self) -> Optional[BacktestResult]:
        """
        Get the most recently created result.

        Returns:
            Latest BacktestResult or None if no results exist
        """
        results = self.list(limit=1)
        if not results:
            return None

        return self.get(results[0]["result_id"])

    def __str__(self) -> str:
        """String representation of store."""
        info = self.get_storage_info()
        return (
            f"ResultsStore(dir='{info['storage_dir']}', "
            f"count={info['result_count']}, "
            f"size={info['total_size_mb']}MB)"
        )
