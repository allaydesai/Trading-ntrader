"""Unit tests for import-verification surfacing (Story 2.5 AC1/AC4).

Two layers:

* ``collect_ohlc_warnings`` — the pure helper that turns a parser
  ``ValidationResult`` into the capped, non-blocking warning list threaded onto
  ``ImportResult``.
* A real ``FirstRateCsvParser`` end-to-end check that ``high < low`` and
  ``volume < 0`` rows are flagged in ``parser.last_validation`` (the raw-row
  layer where the violations remain visible — Nautilus drops them before they
  become bars).

No DB, no network, no Nautilus engine.
"""

from pathlib import Path

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import ValidationResult
from src.services.firstrate.import_verification import (
    MAX_VERIFICATION_WARNINGS,
    collect_ohlc_warnings,
)
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser

pytestmark = pytest.mark.unit


class TestCollectOhlcWarnings:
    def test_none_validation_is_clean(self):
        assert collect_ohlc_warnings(None) == []

    def test_valid_validation_is_clean(self):
        validation = ValidationResult(valid=True, errors=[], row_count=5, invalid_rows=0)
        assert collect_ohlc_warnings(validation) == []

    def test_invalid_validation_surfaces_errors(self):
        validation = ValidationResult(
            valid=False,
            errors=["Row 3: high (90.00) < low (95.00)", "Row 5: volume (-100) < 0"],
            row_count=10,
            invalid_rows=2,
        )
        warnings = collect_ohlc_warnings(validation)
        assert warnings == [
            "Row 3: high (90.00) < low (95.00)",
            "Row 5: volume (-100) < 0",
        ]

    def test_warnings_are_capped(self):
        errors = [f"Row {i}: high < low" for i in range(50)]
        validation = ValidationResult(valid=False, errors=errors, row_count=50, invalid_rows=50)
        warnings = collect_ohlc_warnings(validation, max_warnings=5)
        assert len(warnings) == 6
        assert "truncated" in warnings[-1]

    def test_default_cap_constant(self):
        errors = [f"Row {i}: high < low" for i in range(MAX_VERIFICATION_WARNINGS + 10)]
        validation = ValidationResult(
            valid=False, errors=errors, row_count=len(errors), invalid_rows=len(errors)
        )
        warnings = collect_ohlc_warnings(validation)
        assert len(warnings) == MAX_VERIFICATION_WARNINGS + 1

    def test_exactly_at_cap_not_truncated(self):
        errors = [f"Row {i}: high < low" for i in range(MAX_VERIFICATION_WARNINGS)]
        validation = ValidationResult(
            valid=False, errors=errors, row_count=len(errors), invalid_rows=len(errors)
        )
        warnings = collect_ohlc_warnings(validation)
        assert len(warnings) == MAX_VERIFICATION_WARNINGS
        assert all("truncated" not in w for w in warnings)


class TestParserExposesValidation:
    """Story 2.5 AC1 end-to-end at the raw-row layer (real parser)."""

    @pytest.fixture()
    def parser(self) -> FirstRateCsvParser:
        return FirstRateCsvParser()

    @pytest.fixture()
    def instrument_id(self) -> InstrumentId:
        return InstrumentId.from_str("SPY.ARCA")

    @pytest.fixture()
    def bar_type(self) -> BarType:
        return BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")

    def _write(self, tmp_path: Path, lines: list[str]) -> Path:
        f = tmp_path / "SPY.txt"
        f.write_text("\n".join(lines))
        return f

    def test_high_below_low_flagged_in_last_validation(
        self, parser, instrument_id, bar_type, tmp_path
    ):
        f = self._write(
            tmp_path,
            [
                "2020-01-02,100.00,105.00,99.00,103.00,1000",
                "2020-01-03,100.00,90.00,95.00,92.00,500",  # high < low
            ],
        )
        parser.parse_file(f, instrument_id, bar_type)

        assert parser.last_validation is not None
        assert parser.last_validation.valid is False
        warnings = collect_ohlc_warnings(parser.last_validation)
        assert any("high" in w and "low" in w for w in warnings)

    def test_negative_volume_flagged_in_last_validation(
        self, parser, instrument_id, bar_type, tmp_path
    ):
        f = self._write(
            tmp_path,
            [
                "2020-01-02,100.00,105.00,99.00,103.00,1000",
                "2020-01-03,100.00,105.00,99.00,103.00,-500",  # volume < 0
            ],
        )
        parser.parse_file(f, instrument_id, bar_type)

        assert parser.last_validation is not None
        warnings = collect_ohlc_warnings(parser.last_validation)
        assert any("volume" in w for w in warnings)

    def test_clean_file_has_valid_validation(self, parser, instrument_id, bar_type, tmp_path):
        f = self._write(
            tmp_path,
            [
                "2020-01-02,100.00,105.00,99.00,103.00,1000",
                "2020-01-03,101.00,106.00,100.00,104.00,2000",
            ],
        )
        parser.parse_file(f, instrument_id, bar_type)

        assert parser.last_validation is not None
        assert parser.last_validation.valid is True
        assert collect_ohlc_warnings(parser.last_validation) == []

    def test_empty_file_leaves_validation_none(self, parser, instrument_id, bar_type, tmp_path):
        f = self._write(tmp_path, [])
        parser.parse_file(f, instrument_id, bar_type)
        assert parser.last_validation is None
        assert collect_ohlc_warnings(parser.last_validation) == []
