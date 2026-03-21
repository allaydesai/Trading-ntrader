"""Unit tests for backtest run form data models."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.api.models.run_backtest import BacktestRunFormData, StrategyOption


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
