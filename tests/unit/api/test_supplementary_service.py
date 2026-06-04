"""Unit tests for the supplementary presentation service (Story 4.2).

Covers the pure ``format_split_ratio`` formatter and the
``_build_supplementary_context`` builder using async doubles.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.api.supplementary_service import (
    _build_supplementary_context,
    format_split_ratio,
)


@pytest.mark.unit
class TestFormatSplitRatio:
    """Tests for the Decimal → display-string split ratio formatter."""

    def test_forward_split_4(self):
        assert format_split_ratio(Decimal("4")) == "4:1"

    def test_forward_split_7(self):
        assert format_split_ratio(Decimal("7")) == "7:1"

    def test_reverse_split_half(self):
        assert format_split_ratio(Decimal("0.5")) == "1:2"

    def test_no_op_split_one(self):
        assert format_split_ratio(Decimal("1")) == "1:1"

    def test_non_integral_falls_back_to_decimal_string(self):
        assert format_split_ratio(Decimal("1.5")) == "1.5"

    def test_reverse_third(self):
        assert format_split_ratio(Decimal("0.3333333")) == "1:3"


def _div(ex_date: date, amount: str):
    from src.db.models.catalog_dividend import CatalogDividend

    return CatalogDividend(ticker="AAPL", ex_date=ex_date, amount=Decimal(amount))


def _split(effective_date: date, ratio: str):
    from src.db.models.catalog_stock_split import CatalogStockSplit

    return CatalogStockSplit(ticker="AAPL", effective_date=effective_date, ratio=Decimal(ratio))


def _make_service(instrument):
    svc = AsyncMock()
    svc.get_instrument.return_value = instrument
    return svc


def _make_repo(rows):
    repo = AsyncMock()
    repo.list_by_ticker.return_value = rows
    return repo


@pytest.mark.unit
class TestBuildSupplementaryContext:
    """Tests for the template-context builder."""

    async def test_profile_dividends_splits_present(self):
        from src.db.models.catalog_instrument import CatalogInstrument

        instrument = CatalogInstrument(ticker="AAPL", asset_class="STOCK", catalog_name="e2e-test")
        ctx = await _build_supplementary_context(
            _make_service(instrument),
            _make_repo([_div(date(2026, 2, 9), "0.26")]),
            _make_repo([_split(date(2020, 8, 31), "4")]),
            "e2e-test",
            "AAPL",
        )
        assert ctx["instrument"] is instrument
        assert ctx["has_company_profile"] is True
        assert len(ctx["dividends"]) == 1
        assert len(ctx["splits"]) == 1
        assert ctx["format_split_ratio"] is format_split_ratio
        assert callable(ctx["format_date"])
        assert ctx["format_date"](date(2026, 2, 9)) == "2026-02-09"

    async def test_all_empty(self):
        ctx = await _build_supplementary_context(
            _make_service(None),
            _make_repo([]),
            _make_repo([]),
            "e2e-test",
            "ZZZ",
        )
        assert ctx["instrument"] is None
        assert ctx["has_company_profile"] is False
        assert ctx["dividends"] == []
        assert ctx["splits"] == []

    async def test_missing_profile_but_data_present(self):
        ctx = await _build_supplementary_context(
            _make_service(None),
            _make_repo([_div(date(2026, 2, 9), "0.26")]),
            _make_repo([]),
            "e2e-test",
            "AAPL",
        )
        assert ctx["has_company_profile"] is False
        assert len(ctx["dividends"]) == 1
