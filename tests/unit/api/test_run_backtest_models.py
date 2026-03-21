"""Unit tests for backtest run form data models."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.api.models.run_backtest import (
    BacktestRunFormData,
    StrategyOption,
    StrategyParamField,
    schema_to_fields,
)
from src.models.strategy import MomentumParameters, SMAParameters


class TestStrategyOption:
    """Tests for StrategyOption view model."""

    def test_create_strategy_option(self):
        opt = StrategyOption(name="sma_crossover", description="SMA Crossover Strategy")
        assert opt.name == "sma_crossover"
        assert opt.description == "SMA Crossover Strategy"
        assert opt.aliases == []

    def test_create_with_aliases(self):
        opt = StrategyOption(
            name="sma_crossover",
            description="SMA",
            aliases=["sma", "smacrossover"],
        )
        assert opt.aliases == ["sma", "smacrossover"]


class TestBacktestRunFormData:
    """Tests for BacktestRunFormData validation."""

    def _valid_data(self, **overrides) -> dict:
        """Return valid form data with optional overrides."""
        data = {
            "strategy": "sma_crossover",
            "symbol": "AAPL",
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 12, 31),
        }
        data.update(overrides)
        return data

    def test_valid_form_data(self):
        form = BacktestRunFormData(**self._valid_data())
        assert form.strategy == "sma_crossover"
        assert form.symbol == "AAPL"
        assert form.start_date == date(2024, 1, 1)
        assert form.end_date == date(2024, 12, 31)

    def test_default_values(self):
        form = BacktestRunFormData(**self._valid_data())
        assert form.data_source == "catalog"
        assert form.timeframe == "1-DAY"
        assert form.starting_balance == Decimal("1000000")
        assert form.timeout_seconds == 300
        assert form.strategy_params == {}

    def test_custom_values(self):
        form = BacktestRunFormData(
            **self._valid_data(
                data_source="ibkr",
                timeframe="1-HOUR",
                starting_balance=Decimal("50000"),
                timeout_seconds=600,
                strategy_params={"fast_period": 10},
            )
        )
        assert form.data_source == "ibkr"
        assert form.timeframe == "1-HOUR"
        assert form.starting_balance == Decimal("50000")
        assert form.timeout_seconds == 600
        assert form.strategy_params == {"fast_period": 10}

    def test_missing_strategy_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRunFormData(**self._valid_data(strategy=None))

    def test_empty_strategy_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRunFormData(**self._valid_data(strategy=""))

    def test_missing_symbol_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRunFormData(**self._valid_data(symbol=None))

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRunFormData(**self._valid_data(symbol=""))

    def test_missing_start_date_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRunFormData(**self._valid_data(start_date=None))

    def test_missing_end_date_rejected(self):
        with pytest.raises(ValidationError):
            BacktestRunFormData(**self._valid_data(end_date=None))

    def test_start_date_after_end_date_rejected(self):
        with pytest.raises(ValidationError, match="start_date must be before end_date"):
            BacktestRunFormData(
                **self._valid_data(
                    start_date=date(2024, 12, 31),
                    end_date=date(2024, 1, 1),
                )
            )

    def test_start_date_equals_end_date_rejected(self):
        with pytest.raises(ValidationError, match="start_date must be before end_date"):
            BacktestRunFormData(
                **self._valid_data(
                    start_date=date(2024, 6, 15),
                    end_date=date(2024, 6, 15),
                )
            )

    def test_invalid_data_source_rejected(self):
        with pytest.raises(ValidationError, match="Invalid data_source"):
            BacktestRunFormData(**self._valid_data(data_source="invalid"))

    def test_invalid_timeframe_rejected(self):
        with pytest.raises(ValidationError, match="Invalid timeframe"):
            BacktestRunFormData(**self._valid_data(timeframe="2-HOUR"))

    def test_starting_balance_zero_rejected(self):
        with pytest.raises(ValidationError, match="starting_balance must be greater than 0"):
            BacktestRunFormData(**self._valid_data(starting_balance=Decimal("0")))

    def test_starting_balance_negative_rejected(self):
        with pytest.raises(ValidationError, match="starting_balance must be greater than 0"):
            BacktestRunFormData(**self._valid_data(starting_balance=Decimal("-100")))

    def test_timeout_seconds_zero_rejected(self):
        with pytest.raises(ValidationError, match="timeout_seconds must be greater than 0"):
            BacktestRunFormData(**self._valid_data(timeout_seconds=0))

    def test_timeout_seconds_negative_rejected(self):
        with pytest.raises(ValidationError, match="timeout_seconds must be greater than 0"):
            BacktestRunFormData(**self._valid_data(timeout_seconds=-10))

    @pytest.mark.parametrize("source", ["catalog", "ibkr", "kraken", "mock"])
    def test_all_valid_data_sources(self, source):
        form = BacktestRunFormData(**self._valid_data(data_source=source))
        assert form.data_source == source

    @pytest.mark.parametrize(
        "tf",
        ["1-MINUTE", "5-MINUTE", "15-MINUTE", "1-HOUR", "4-HOUR", "1-DAY", "1-WEEK"],
    )
    def test_all_valid_timeframes(self, tf):
        form = BacktestRunFormData(**self._valid_data(timeframe=tf))
        assert form.timeframe == tf


class TestStrategyParamField:
    """Tests for StrategyParamField view model."""

    def test_create_integer_field(self):
        field = StrategyParamField(
            name="fast_period",
            field_type="integer",
            default=10,
            description="Fast SMA period",
            minimum=1,
            maximum=200,
        )
        assert field.name == "fast_period"
        assert field.field_type == "integer"
        assert field.default == 10
        assert field.description == "Fast SMA period"
        assert field.minimum == 1
        assert field.maximum == 200
        assert field.required is False

    def test_create_number_field(self):
        field = StrategyParamField(
            name="portfolio_value",
            field_type="number",
            default="1000000",
            description="Starting portfolio value in USD",
        )
        assert field.field_type == "number"
        assert field.default == "1000000"

    def test_create_boolean_field(self):
        field = StrategyParamField(
            name="allow_short",
            field_type="boolean",
            default=False,
            description="Allow short selling",
        )
        assert field.field_type == "boolean"
        assert field.default is False

    def test_create_string_field(self):
        field = StrategyParamField(
            name="order_id_tag",
            field_type="string",
            default="002",
            description="Tag for order IDs",
        )
        assert field.field_type == "string"
        assert field.default == "002"

    def test_defaults(self):
        field = StrategyParamField(name="test", field_type="integer")
        assert field.default is None
        assert field.description == ""
        assert field.minimum is None
        assert field.maximum is None
        assert field.required is False


class TestSchemaToFields:
    """Tests for schema_to_fields helper function."""

    def test_sma_parameters_field_count(self):
        fields = schema_to_fields(SMAParameters)
        assert len(fields) == 4

    def test_sma_parameters_integer_fields(self):
        fields = schema_to_fields(SMAParameters)
        by_name = {f.name: f for f in fields}

        fast = by_name["fast_period"]
        assert fast.field_type == "integer"
        assert fast.default == 10
        assert fast.minimum == 1
        assert fast.maximum == 200
        assert fast.description == "Fast SMA period"

        slow = by_name["slow_period"]
        assert slow.field_type == "integer"
        assert slow.default == 20
        assert slow.minimum == 1
        assert slow.maximum == 200

    def test_sma_parameters_decimal_fields(self):
        fields = schema_to_fields(SMAParameters)
        by_name = {f.name: f for f in fields}

        pv = by_name["portfolio_value"]
        assert pv.field_type == "number"
        assert pv.default == "1000000"
        assert pv.description == "Starting portfolio value in USD"

        psp = by_name["position_size_pct"]
        assert psp.field_type == "number"
        assert psp.default == "10.0"
        assert psp.minimum == 0.1
        assert psp.maximum == 100.0

    def test_momentum_parameters_field_count(self):
        fields = schema_to_fields(MomentumParameters)
        assert len(fields) == 6

    def test_momentum_parameters_boolean_field(self):
        fields = schema_to_fields(MomentumParameters)
        by_name = {f.name: f for f in fields}

        allow_short = by_name["allow_short"]
        assert allow_short.field_type == "boolean"
        assert allow_short.default is False
        assert allow_short.description == "Allow short selling"

    def test_momentum_parameters_string_field(self):
        fields = schema_to_fields(MomentumParameters)
        by_name = {f.name: f for f in fields}

        tag = by_name["order_id_tag"]
        assert tag.field_type == "string"
        assert tag.default == "002"

    def test_momentum_parameters_integer_fields(self):
        fields = schema_to_fields(MomentumParameters)
        by_name = {f.name: f for f in fields}

        assert by_name["fast_period"].field_type == "integer"
        assert by_name["fast_period"].default == 20
        assert by_name["fast_period"].minimum == 1

        assert by_name["slow_period"].default == 50
        assert by_name["warmup_days"].default == 1
        assert by_name["warmup_days"].minimum == 0

    def test_descriptions_extracted(self):
        fields = schema_to_fields(SMAParameters)
        for field in fields:
            assert field.description != "", f"Field {field.name} missing description"
