"""JSON export service for trading data and reports."""

import json
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Union

from ...models.trade import TradeModel
from .exceptions import (
    FileWriteError,
    EmptyDataError,
    SerializationError,
)
from .validators import FileValidator, TradeValidator


class JSONExporter:
    """JSON export functionality for trading data."""

    def __init__(self, output_dir: Union[str, Path] = "exports"):
        """Initialize JSON exporter.

        Args:
            output_dir: Directory to save JSON files

        Raises:
            DirectoryError: If directory cannot be created or accessed
        """
        self.output_dir = FileValidator.validate_output_directory(output_dir)

    def _serialize_value(self, value: Any) -> Any:
        """Convert non-JSON-serializable values to JSON-compatible formats.

        Args:
            value: Value to serialize

        Returns:
            JSON-serializable value
        """
        if isinstance(value, Decimal):
            return float(value)
        elif isinstance(value, datetime):
            return value.isoformat()
        elif hasattr(value, "__dict__"):
            # Convert objects to dictionaries
            return {k: self._serialize_value(v) for k, v in value.__dict__.items()}
        elif isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]
        elif isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        else:
            return value

    def export_trades(self, trades: List[TradeModel], filename: str = None) -> Path:
        """Export trades to JSON file.

        Args:
            trades: List of trades to export
            filename: Output filename (optional)

        Returns:
            Path to exported file

        Raises:
            EmptyDataError: If trades list is empty or None
            ValidationError: If trade data validation fails
            InvalidDataError: If filename is invalid
            FileWriteError: If file writing fails
        """
        # Validate inputs - allow empty trades for some use cases
        if trades is None:
            raise EmptyDataError("trades")
        if trades:  # Only validate non-empty lists
            TradeValidator.validate_trade_list(trades)
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"trades_export_{timestamp}.json"

        # Validate filename
        FileValidator.validate_filename(filename)
        FileValidator.validate_file_extension(filename, [".json"])

        filepath = self.output_dir / filename

        # Convert trades to dictionaries
        trades_data = []
        for trade in trades:
            trade_dict = {
                "position_id": trade.position_id,
                "instrument_id": trade.instrument_id,
                "side": trade.side,
                "quantity": self._serialize_value(trade.quantity),
                "entry_price": self._serialize_value(trade.entry_price),
                "exit_price": self._serialize_value(trade.exit_price),
                "entry_time": self._serialize_value(trade.entry_time),
                "exit_time": self._serialize_value(trade.exit_time),
                "realized_pnl": self._serialize_value(trade.realized_pnl),
                "pnl_pct": self._serialize_value(trade.pnl_pct),
                "commission": self._serialize_value(trade.commission),
                "slippage": self._serialize_value(trade.slippage),
                "strategy_name": trade.strategy_name,
                "notes": trade.notes,
                "duration_hours": trade.duration_hours,
                "is_winning_trade": trade.is_winning_trade,
            }
            trades_data.append(trade_dict)

        # Write to JSON file with proper formatting
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "export_metadata": {
                            "export_time": datetime.now().isoformat(),
                            "total_trades": len(trades),
                            "format_version": "1.0",
                        },
                        "trades": trades_data,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except OSError as e:
            raise FileWriteError(str(filepath), f"OS error: {str(e)}") from e
        except (TypeError, ValueError) as e:
            raise SerializationError(
                "trades", f"JSON serialization failed: {str(e)}"
            ) from e
        except Exception as e:
            raise FileWriteError(str(filepath), f"Unexpected error: {str(e)}") from e

        return filepath

    def export_performance_report(
        self, report_data: Dict[str, Any], filename: str = None
    ) -> Path:
        """Export performance report to JSON file.

        Args:
            report_data: Performance report data
            filename: Output filename (optional)

        Returns:
            Path to exported file
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"performance_report_{timestamp}.json"

        filepath = self.output_dir / filename

        # Serialize the report data
        serialized_data = {
            "export_metadata": {
                "export_time": datetime.now().isoformat(),
                "report_type": "performance",
                "format_version": "1.0",
            },
            "performance_data": self._serialize_value(report_data),
        }

        # Write to JSON file with proper formatting
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serialized_data, f, indent=2, ensure_ascii=False)

        return filepath

    def export_portfolio_summary(
        self, portfolio_data: Dict[str, Any], filename: str = None
    ) -> Path:
        """Export portfolio summary to JSON file.

        Args:
            portfolio_data: Portfolio summary data
            filename: Output filename (optional)

        Returns:
            Path to exported file
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"portfolio_summary_{timestamp}.json"

        filepath = self.output_dir / filename

        # Serialize the portfolio data
        serialized_data = {
            "export_metadata": {
                "export_time": datetime.now().isoformat(),
                "report_type": "portfolio_summary",
                "format_version": "1.0",
            },
            "portfolio_data": self._serialize_value(portfolio_data),
        }

        # Write to JSON file with proper formatting
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serialized_data, f, indent=2, ensure_ascii=False)

        return filepath

    def load_json_file(self, filepath: Union[str, Path]) -> Dict[str, Any]:
        """Load and parse JSON file.

        Args:
            filepath: Path to JSON file

        Returns:
            Parsed JSON data

        Raises:
            FileNotFoundError: If file doesn't exist
            json.JSONDecodeError: If file is not valid JSON
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"JSON file not found: {filepath}")

        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
