"""Unit tests for IBKRCommissionModel construction from Settings."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.core.fee_models import IBKRCommissionModel

_SETTINGS = SimpleNamespace(
    commission_per_share=Decimal("0.005"),
    commission_min_per_order=Decimal("1.00"),
    commission_max_rate=Decimal("0.005"),
    slippage_per_share=Decimal("0.01"),
)


@pytest.mark.unit
class TestFromSettings:
    """``from_settings`` carries every cost term, scaled together."""

    def test_unscaled_terms_match_settings(self) -> None:
        model = IBKRCommissionModel.from_settings(_SETTINGS)

        assert model.commission_per_share == Decimal("0.005")
        assert model.min_per_order == Decimal("1.00")
        assert model.max_rate == Decimal("0.005")
        assert model.slippage_per_share == Decimal("0.01")

    def test_scale_applies_to_commission_and_slippage_alike(self) -> None:
        model = IBKRCommissionModel.from_settings(_SETTINGS, Decimal("2"))

        assert model.commission_per_share == Decimal("0.010")
        assert model.min_per_order == Decimal("2.00")
        assert model.max_rate == Decimal("0.010")
        assert model.slippage_per_share == Decimal("0.02")

    def test_negative_slippage_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="slippage_per_share"):
            IBKRCommissionModel(slippage_per_share=Decimal("-0.01"))
