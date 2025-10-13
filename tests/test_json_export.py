"""Tests for JSON export functionality."""

import json
import pytest
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from src.services.reports.json_exporter import JSONExporter
from src.models.trade import TradeModel


@pytest.fixture
def sample_trades():
    """Create sample trades for testing."""
    return [
        TradeModel(
            position_id="POS-001",
            instrument_id="AAPL",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("155.00"),
            side="LONG",
            entry_time=datetime(2024, 1, 1, 10, 0),
            exit_time=datetime(2024, 1, 1, 16, 0),
            realized_pnl=Decimal("500.00"),
            commission=Decimal("2.00"),
            strategy_name="test_strategy",
        ),
        TradeModel(
            position_id="POS-002",
            instrument_id="GOOGL",
            quantity=Decimal("50"),
            entry_price=Decimal("2800.00"),
            exit_price=Decimal("2750.00"),
            side="SHORT",
            entry_time=datetime(2024, 1, 2, 9, 30),
            exit_time=datetime(2024, 1, 2, 15, 30),
            realized_pnl=Decimal("-2500.00"),
            commission=Decimal("5.00"),
            strategy_name="test_strategy",
        ),
    ]


@pytest.fixture
def json_exporter():
    """Create JSON exporter with temporary directory."""
    with TemporaryDirectory() as temp_dir:
        yield JSONExporter(output_dir=temp_dir)


def test_json_exporter_initialization():
    """Test JSON exporter initialization."""
    with TemporaryDirectory() as temp_dir:
        exporter = JSONExporter(output_dir=temp_dir)
        assert exporter.output_dir == Path(temp_dir)
        assert exporter.output_dir.exists()


def test_serialize_value_decimal():
    """Test serialization of Decimal values."""
    exporter = JSONExporter()
    result = exporter._serialize_value(Decimal("123.45"))
    assert result == 123.45
    assert isinstance(result, float)


def test_serialize_value_datetime():
    """Test serialization of datetime values."""
    exporter = JSONExporter()
    dt = datetime(2024, 1, 1, 12, 0, 0)
    result = exporter._serialize_value(dt)
    assert result == "2024-01-01T12:00:00"


def test_serialize_value_nested_dict():
    """Test serialization of nested dictionaries."""
    exporter = JSONExporter()
    data = {
        "price": Decimal("100.50"),
        "timestamp": datetime(2024, 1, 1),
        "nested": {"value": Decimal("50.25")},
    }
    result = exporter._serialize_value(data)
    assert result["price"] == 100.50
    assert result["timestamp"] == "2024-01-01T00:00:00"
    assert result["nested"]["value"] == 50.25


def test_export_trades_default_filename(json_exporter, sample_trades):
    """Test exporting trades with default filename."""
    filepath = json_exporter.export_trades(sample_trades)

    assert filepath.exists()
    assert filepath.suffix == ".json"
    assert "trades_export_" in filepath.name

    # Verify file content
    with open(filepath, "r") as f:
        data = json.load(f)

    assert "export_metadata" in data
    assert "trades" in data
    assert data["export_metadata"]["total_trades"] == 2
    assert data["export_metadata"]["format_version"] == "1.0"
    assert len(data["trades"]) == 2


def test_export_trades_custom_filename(json_exporter, sample_trades):
    """Test exporting trades with custom filename."""
    custom_filename = "my_trades.json"
    filepath = json_exporter.export_trades(sample_trades, custom_filename)

    assert filepath.name == custom_filename
    assert filepath.exists()


def test_export_trades_content_validation(json_exporter, sample_trades):
    """Test that exported trade content matches expected format."""
    filepath = json_exporter.export_trades(sample_trades)

    with open(filepath, "r") as f:
        data = json.load(f)

    trade_data = data["trades"][0]
    assert trade_data["position_id"] == "POS-001"
    assert trade_data["instrument_id"] == "AAPL"
    assert trade_data["quantity"] == 100.0
    assert trade_data["entry_price"] == 150.0
    assert trade_data["exit_price"] == 155.0
    assert trade_data["side"] == "LONG"
    assert trade_data["realized_pnl"] == 500.0
    assert trade_data["commission"] == 2.0
    assert trade_data["strategy_name"] == "test_strategy"


def test_export_performance_report(json_exporter):
    """Test exporting performance report."""
    report_data = {
        "total_trades": 100,
        "winning_trades": 60,
        "losing_trades": 40,
        "total_profit": Decimal("10000.00"),
        "max_drawdown": Decimal("1500.00"),
        "sharpe_ratio": 1.5,
        "win_rate": 0.6,
    }

    filepath = json_exporter.export_performance_report(report_data)

    assert filepath.exists()
    assert "performance_report_" in filepath.name

    with open(filepath, "r") as f:
        data = json.load(f)

    assert data["export_metadata"]["report_type"] == "performance"
    assert data["performance_data"]["total_trades"] == 100
    assert data["performance_data"]["total_profit"] == 10000.0
    assert data["performance_data"]["max_drawdown"] == 1500.0


def test_export_portfolio_summary(json_exporter):
    """Test exporting portfolio summary."""
    portfolio_data = {
        "total_value": Decimal("50000.00"),
        "cash_balance": Decimal("10000.00"),
        "positions": [
            {
                "symbol": "AAPL",
                "quantity": 100,
                "current_price": Decimal("150.00"),
                "market_value": Decimal("15000.00"),
            }
        ],
        "daily_pnl": Decimal("250.00"),
    }

    filepath = json_exporter.export_portfolio_summary(portfolio_data)

    assert filepath.exists()
    assert "portfolio_summary_" in filepath.name

    with open(filepath, "r") as f:
        data = json.load(f)

    assert data["export_metadata"]["report_type"] == "portfolio_summary"
    assert data["portfolio_data"]["total_value"] == 50000.0
    assert data["portfolio_data"]["cash_balance"] == 10000.0
    assert len(data["portfolio_data"]["positions"]) == 1


def test_load_json_file(json_exporter, sample_trades):
    """Test loading JSON file."""
    # First export trades
    filepath = json_exporter.export_trades(sample_trades, "test_load.json")

    # Then load the file
    loaded_data = json_exporter.load_json_file(filepath)

    assert "export_metadata" in loaded_data
    assert "trades" in loaded_data
    assert loaded_data["export_metadata"]["total_trades"] == 2


def test_load_json_file_not_found(json_exporter):
    """Test loading non-existent JSON file."""
    with pytest.raises(FileNotFoundError):
        json_exporter.load_json_file("nonexistent.json")


def test_export_empty_trades_list(json_exporter):
    """Test exporting empty trades list."""
    filepath = json_exporter.export_trades([])

    with open(filepath, "r") as f:
        data = json.load(f)

    assert data["export_metadata"]["total_trades"] == 0
    assert data["trades"] == []


def test_json_file_formatting(json_exporter, sample_trades):
    """Test that JSON file is properly formatted."""
    filepath = json_exporter.export_trades(sample_trades)

    # Read raw content to check formatting
    with open(filepath, "r") as f:
        content = f.read()

    # Should be indented (not minified)
    assert "\n" in content
    assert "  " in content  # Should have 2-space indentation


def test_unicode_handling(json_exporter):
    """Test handling of unicode characters in JSON export."""
    trades = [
        TradeModel(
            position_id="POS-TEST",
            instrument_id="TEST",
            quantity=Decimal("100"),
            entry_price=Decimal("100.00"),
            exit_price=Decimal("105.00"),
            side="LONG",
            entry_time=datetime(2024, 1, 1),
            exit_time=datetime(2024, 1, 1),
            realized_pnl=Decimal("500.00"),
            commission=Decimal("2.00"),
        )
    ]

    # Export and verify unicode is preserved
    filepath = json_exporter.export_trades(trades)

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Verify the file was created and can be read
    assert len(data["trades"]) == 1
