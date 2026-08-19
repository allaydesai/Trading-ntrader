"""Unit tests for the trading-session transition exception (Story 2.3, AC #3).

``InvalidSessionTransition`` joins the ``BacktestStorageError`` family already
used by every other storage-layer failure, rather than becoming a second
exception hierarchy — mirrors ``tests/unit/services/test_exceptions.py``'s
``TestExceptionHierarchy``.
"""

import pytest

from src.db.exceptions import BacktestStorageError, InvalidSessionTransition


class TestInvalidSessionTransition:
    """AC #3: raised for both an illegal edge and a refused reclaim."""

    @pytest.mark.unit
    def test_is_a_subclass_of_backtest_storage_error(self):
        assert issubclass(InvalidSessionTransition, BacktestStorageError)

    @pytest.mark.unit
    def test_is_catchable_as_backtest_storage_error(self):
        with pytest.raises(BacktestStorageError):
            raise InvalidSessionTransition("created -> sealed is not a legal transition")

    @pytest.mark.unit
    def test_carries_its_message(self):
        err = InvalidSessionTransition("sealed is terminal")
        assert str(err) == "sealed is terminal"
