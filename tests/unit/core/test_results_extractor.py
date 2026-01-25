"""Tests for ResultsExtractor."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.core.results_extractor import (
    ResultsExtractor,
    _calculate_cagr,
    _calculate_calmar_ratio,
    _calculate_max_drawdown,
    _safe_float,
)


class TestSafeFloat:
    """Tests for _safe_float helper function."""

    def test_safe_float_with_valid_number(self):
        """Test safe_float with valid number."""
        assert _safe_float(1.5) == 1.5

    def test_safe_float_with_integer(self):
        """Test safe_float with integer."""
        assert _safe_float(10) == 10.0

    def test_safe_float_with_string_number(self):
        """Test safe_float with string number."""
        assert _safe_float("3.14") == 3.14

    def test_safe_float_with_none(self):
        """Test safe_float with None."""
        assert _safe_float(None) is None

    def test_safe_float_with_empty_string(self):
        """Test safe_float with empty string."""
        assert _safe_float("") is None

    def test_safe_float_with_nan(self):
        """Test safe_float with NaN."""
        assert _safe_float(float("nan")) is None

    def test_safe_float_with_infinity(self):
        """Test safe_float with infinity."""
        assert _safe_float(float("inf")) is None
        assert _safe_float(float("-inf")) is None

    def test_safe_float_with_invalid_string(self):
        """Test safe_float with invalid string."""
        assert _safe_float("not_a_number") is None


class TestCalculateCagr:
    """Tests for _calculate_cagr helper function."""

    def test_calculate_cagr_positive_return(self):
        """Test CAGR calculation with positive return."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        cagr = _calculate_cagr(100000, 110000, start, end)
        assert cagr is not None
        assert abs(cagr - 0.1) < 0.01  # ~10% CAGR

    def test_calculate_cagr_negative_return(self):
        """Test CAGR calculation with negative return."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        cagr = _calculate_cagr(100000, 90000, start, end)
        assert cagr is not None
        assert cagr < 0

    def test_calculate_cagr_multi_year(self):
        """Test CAGR calculation over multiple years."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2022, 1, 1, tzinfo=timezone.utc)
        # Double in 2 years = ~41% CAGR
        cagr = _calculate_cagr(100000, 200000, start, end)
        assert cagr is not None
        assert abs(cagr - 0.41) < 0.02

    def test_calculate_cagr_zero_starting_balance(self):
        """Test CAGR returns None for zero starting balance."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        assert _calculate_cagr(0, 100000, start, end) is None

    def test_calculate_cagr_zero_final_balance(self):
        """Test CAGR returns None for zero final balance."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        assert _calculate_cagr(100000, 0, start, end) is None

    def test_calculate_cagr_same_date(self):
        """Test CAGR returns None for same start/end date."""
        date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert _calculate_cagr(100000, 110000, date, date) is None


class TestCalculateCalmarRatio:
    """Tests for _calculate_calmar_ratio helper function."""

    def test_calmar_ratio_positive(self):
        """Test Calmar ratio with positive CAGR and negative drawdown."""
        calmar = _calculate_calmar_ratio(0.15, -0.10)  # 15% CAGR, -10% drawdown
        assert calmar is not None
        assert abs(calmar - 1.5) < 0.01

    def test_calmar_ratio_none_cagr(self):
        """Test Calmar ratio with None CAGR."""
        assert _calculate_calmar_ratio(None, -0.10) is None

    def test_calmar_ratio_none_drawdown(self):
        """Test Calmar ratio with None max drawdown."""
        assert _calculate_calmar_ratio(0.15, None) is None

    def test_calmar_ratio_zero_drawdown(self):
        """Test Calmar ratio with zero max drawdown."""
        assert _calculate_calmar_ratio(0.15, 0.0) is None


class TestCalculateMaxDrawdown:
    """Tests for _calculate_max_drawdown helper function."""

    def test_max_drawdown_with_returns(self):
        """Test max drawdown calculation with returns series."""
        import pandas as pd

        mock_analyzer = MagicMock()
        # Simulate returns that go up then down: 10%, -5%, -10%, 5%
        returns = pd.Series([0.10, -0.05, -0.10, 0.05])
        mock_analyzer.returns.return_value = returns

        drawdown = _calculate_max_drawdown(mock_analyzer)
        assert drawdown is not None
        assert drawdown < 0  # Drawdown should be negative

    def test_max_drawdown_empty_returns(self):
        """Test max drawdown with empty returns."""
        import pandas as pd

        mock_analyzer = MagicMock()
        mock_analyzer.returns.return_value = pd.Series([])

        assert _calculate_max_drawdown(mock_analyzer) is None

    def test_max_drawdown_none_returns(self):
        """Test max drawdown with None returns."""
        mock_analyzer = MagicMock()
        mock_analyzer.returns.return_value = None

        assert _calculate_max_drawdown(mock_analyzer) is None


class TestResultsExtractor:
    """Tests for ResultsExtractor class."""

    @pytest.fixture
    def mock_engine(self):
        """Create a mock backtest engine."""
        engine = MagicMock()
        return engine

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.default_balance = 1000000
        return settings

    def test_extract_results_no_engine(self, mock_settings):
        """Test extract_results returns empty result when no engine."""
        extractor = ResultsExtractor(engine=None, settings=mock_settings)
        result = extractor.extract_results()

        assert result.total_trades == 0
        assert result.total_return == 0.0

    def test_extract_results_no_account(self, mock_engine, mock_settings):
        """Test extract_results when no account found."""
        mock_engine.cache.account_for_venue.return_value = None
        extractor = ResultsExtractor(engine=mock_engine, settings=mock_settings)
        result = extractor.extract_results()

        assert result.total_trades == 0

    def test_extract_equity_curve_no_engine(self, mock_settings):
        """Test extract_equity_curve returns empty list when no engine."""
        extractor = ResultsExtractor(engine=None, settings=mock_settings)
        curve = extractor.extract_equity_curve()

        assert curve == []
