"""Tests for backtest_orchestrator module."""

from decimal import Decimal

from src.core.backtest_orchestrator import _make_json_serializable


class TestMakeJsonSerializable:
    """Test cases for _make_json_serializable utility function."""

    def test_converts_decimal_to_string(self) -> None:
        """Test that Decimal values are converted to strings."""
        result = _make_json_serializable(Decimal("100.50"))
        assert result == "100.50"
        assert isinstance(result, str)

    def test_handles_nested_dict_with_decimals(self) -> None:
        """Test that nested dictionaries have their Decimal values converted."""
        input_data = {
            "price": Decimal("150.00"),
            "nested": {"qty": Decimal("10")},
        }
        result = _make_json_serializable(input_data)
        assert result == {"price": "150.00", "nested": {"qty": "10"}}
        assert isinstance(result["price"], str)
        assert isinstance(result["nested"]["qty"], str)

    def test_handles_list_with_decimals(self) -> None:
        """Test that lists have their Decimal values converted."""
        input_data = [Decimal("1.0"), Decimal("2.0")]
        result = _make_json_serializable(input_data)
        assert result == ["1.0", "2.0"]
        assert all(isinstance(item, str) for item in result)

    def test_preserves_non_decimal_types(self) -> None:
        """Test that non-Decimal types are preserved unchanged."""
        input_data = {"name": "test", "count": 5, "active": True, "ratio": 0.5}
        result = _make_json_serializable(input_data)
        assert result == input_data
        assert isinstance(result["name"], str)
        assert isinstance(result["count"], int)
        assert isinstance(result["active"], bool)
        assert isinstance(result["ratio"], float)

    def test_handles_mixed_nested_structure(self) -> None:
        """Test complex nested structures with mixed types."""
        input_data = {
            "metrics": {
                "total_return": Decimal("0.1523"),
                "sharpe_ratio": 1.45,
                "positions": [
                    {"symbol": "AAPL", "value": Decimal("10000.50")},
                    {"symbol": "GOOG", "value": Decimal("15000.75")},
                ],
            },
            "metadata": {"version": "1.0", "count": 2},
        }
        result = _make_json_serializable(input_data)

        assert result["metrics"]["total_return"] == "0.1523"
        assert result["metrics"]["sharpe_ratio"] == 1.45
        assert result["metrics"]["positions"][0]["value"] == "10000.50"
        assert result["metrics"]["positions"][1]["value"] == "15000.75"
        assert result["metadata"]["version"] == "1.0"
        assert result["metadata"]["count"] == 2

    def test_handles_empty_dict(self) -> None:
        """Test that empty dictionaries are handled correctly."""
        result = _make_json_serializable({})
        assert result == {}

    def test_handles_empty_list(self) -> None:
        """Test that empty lists are handled correctly."""
        result = _make_json_serializable([])
        assert result == []

    def test_handles_none_value(self) -> None:
        """Test that None values are preserved."""
        input_data = {"value": None, "decimal": Decimal("1.0")}
        result = _make_json_serializable(input_data)
        assert result == {"value": None, "decimal": "1.0"}

    def test_handles_decimal_with_many_decimal_places(self) -> None:
        """Test that Decimals with high precision are preserved."""
        result = _make_json_serializable(Decimal("3.141592653589793238"))
        assert result == "3.141592653589793238"

    def test_handles_negative_decimal(self) -> None:
        """Test that negative Decimal values are converted correctly."""
        result = _make_json_serializable(Decimal("-500.25"))
        assert result == "-500.25"


class TestCostStress:
    """Phase 4 cost stress: the multiplier scales every commission term, and only a stressed
    run's snapshot carries it."""

    def _request(self, **overrides):
        from datetime import datetime, timezone

        from src.models.backtest_request import BacktestRequest

        fields = dict(
            strategy_type="sma_crossover",
            strategy_path="src.x:Y",
            strategy_config={"fast_period": 5},
            symbol="QQQ",
            instrument_id="QQQ.NAMED_CATALOG",
            start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2020, 12, 31, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            catalog_name="e2e-test",
        )
        return BacktestRequest(**{**fields, **overrides})

    def test_the_fee_model_scales_rate_minimum_and_cap_alike(self) -> None:
        from src.core.backtest_orchestrator import BacktestOrchestrator

        orchestrator = BacktestOrchestrator()
        plain, doubled = orchestrator._make_fee_model(), orchestrator._make_fee_model(2.0)
        assert doubled.commission_per_share == plain.commission_per_share * 2
        assert doubled.min_per_order == plain.min_per_order * 2
        assert doubled.max_rate == plain.max_rate * 2
        assert plain.commission_per_share == orchestrator.settings.commission_per_share

    def test_only_a_stressed_run_records_its_multiplier(self) -> None:
        from src.core.backtest_orchestrator import _run_record_fields

        plain = _run_record_fields(self._request())["config_snapshot"]
        stressed = _run_record_fields(self._request(cost_multiplier=2.0))["config_snapshot"]
        assert "cost_multiplier" not in plain
        assert stressed["cost_multiplier"] == 2.0
