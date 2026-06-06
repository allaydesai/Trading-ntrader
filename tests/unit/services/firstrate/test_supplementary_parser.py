"""Unit tests for FirstRate dividend/split supplementary parsers."""

from datetime import date
from decimal import Decimal

import pytest

from src.services.firstrate.parsers.supplementary_parser import (
    DividendRow,
    SplitRow,
    parse_dividends,
    parse_splits,
)


@pytest.mark.unit
class TestParseDividends:
    """Tests for parse_dividends."""

    def test_happy_path(self, tmp_path):
        """Parses a well-formed dividend file into typed rows."""
        f = tmp_path / "AAPL_divs.txt"
        f.write_text("2026-02-09,0.26\n2025-08-11,0.260\n2024-02-09,0.24\n")

        rows = parse_dividends(f)

        assert rows == [
            DividendRow(ex_date=date(2026, 2, 9), amount=Decimal("0.26")),
            DividendRow(ex_date=date(2025, 8, 11), amount=Decimal("0.260")),
            DividendRow(ex_date=date(2024, 2, 9), amount=Decimal("0.24")),
        ]

    def test_reverse_chronological_order_preserved(self, tmp_path):
        """Source ordering (reverse-chronological) is preserved as-is."""
        f = tmp_path / "AAPL_divs.txt"
        f.write_text("2026-02-09,0.26\n2025-08-11,0.26\n")

        rows = parse_dividends(f)

        assert [r.ex_date for r in rows] == [date(2026, 2, 9), date(2025, 8, 11)]

    def test_amount_precision_preserved(self, tmp_path):
        """0.260 and 0.26 both parse to exact Decimal preserving precision."""
        f = tmp_path / "AAPL_divs.txt"
        f.write_text("2025-08-11,0.260\n2026-02-09,0.26\n")

        rows = parse_dividends(f)

        assert rows[0].amount == Decimal("0.260")
        assert str(rows[0].amount) == "0.260"
        assert rows[1].amount == Decimal("0.26")
        assert str(rows[1].amount) == "0.26"

    def test_blank_lines_skipped(self, tmp_path):
        """Blank lines are skipped, not parsed."""
        f = tmp_path / "AAPL_divs.txt"
        f.write_text("2026-02-09,0.26\n\n  \n2024-02-09,0.24\n")

        rows = parse_dividends(f)

        assert len(rows) == 2

    def test_malformed_line_skipped_not_raised(self, tmp_path):
        """A malformed line is logged and skipped, not raised."""
        f = tmp_path / "AAPL_divs.txt"
        f.write_text("2026-02-09,0.26\nGARBAGE\n2024-02-09,not_a_number\n2023-01-01,0.20\n")

        rows = parse_dividends(f)

        # Only the two valid lines parse.
        assert len(rows) == 2
        assert rows[0].ex_date == date(2026, 2, 9)
        assert rows[1].ex_date == date(2023, 1, 1)

    def test_empty_file_returns_empty(self, tmp_path):
        """An empty file yields an empty list."""
        f = tmp_path / "AAPL_divs.txt"
        f.write_text("")

        assert parse_dividends(f) == []

    def test_duplicate_ex_date_deduplicated_last_wins(self, tmp_path):
        """A repeated ex_date (FirstRate trailing-dup block) yields one row.

        Real NVDA_divs.txt repeats 2026-03-11. The unique constraint forbids
        two rows with the same (catalog, ticker, ex_date), so the parser
        dedups by date keeping the last occurrence (matching the bar parser).
        """
        f = tmp_path / "NVDA_divs.txt"
        f.write_text("2026-03-11,0.01\n2025-12-04,0.05\n2026-03-11,0.010\n")

        rows = parse_dividends(f)

        ex_dates = [r.ex_date for r in rows]
        assert ex_dates.count(date(2026, 3, 11)) == 1
        assert len(rows) == 2
        # Last occurrence wins.
        dup = next(r for r in rows if r.ex_date == date(2026, 3, 11))
        assert dup.amount == Decimal("0.010")

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "Infinity"])
    def test_non_finite_amount_skipped(self, tmp_path, bad):
        """``Decimal('nan')``/``Decimal('inf')`` do not raise — reject them.

        These would otherwise pass the parser and corrupt the Numeric column.
        """
        f = tmp_path / "AAPL_divs.txt"
        f.write_text(f"2026-02-09,{bad}\n2024-02-09,0.24\n")

        rows = parse_dividends(f)

        assert len(rows) == 1
        assert rows[0].ex_date == date(2024, 2, 9)


@pytest.mark.unit
class TestParseSplits:
    """Tests for parse_splits."""

    def test_happy_path_aapl(self, tmp_path):
        """Parses the real AAPL split file shape (comma-delimited)."""
        f = tmp_path / "AAPL.txt"
        f.write_text("2020-08-31,4\n2014-06-09,7\n2005-02-28,2\n2000-06-21,2\n")

        rows = parse_splits(f)

        assert rows == [
            SplitRow(effective_date=date(2020, 8, 31), ratio=Decimal("4")),
            SplitRow(effective_date=date(2014, 6, 9), ratio=Decimal("7")),
            SplitRow(effective_date=date(2005, 2, 28), ratio=Decimal("2")),
            SplitRow(effective_date=date(2000, 6, 21), ratio=Decimal("2")),
        ]

    def test_reverse_split_ratio_below_one(self, tmp_path):
        """Reverse splits (ratio < 1) parse correctly as Decimal."""
        f = tmp_path / "RVRS.txt"
        f.write_text("2023-05-01,0.5\n")

        rows = parse_splits(f)

        assert rows[0].ratio == Decimal("0.5")
        assert rows[0].ratio < 1

    def test_split_on_comma_not_slash(self, tmp_path):
        """Files are CSV — a slash-delimited line is malformed and skipped."""
        f = tmp_path / "AAPL.txt"
        f.write_text("2020-08-31/4\n2014-06-09,7\n")

        rows = parse_splits(f)

        # The slash line is malformed → skipped; only the comma line parses.
        assert len(rows) == 1
        assert rows[0].effective_date == date(2014, 6, 9)

    def test_blank_lines_skipped(self, tmp_path):
        """Blank lines are skipped."""
        f = tmp_path / "AAPL.txt"
        f.write_text("2020-08-31,4\n\n2014-06-09,7\n")

        assert len(parse_splits(f)) == 2

    def test_malformed_line_skipped_not_raised(self, tmp_path):
        """A malformed line is skipped, not raised."""
        f = tmp_path / "AAPL.txt"
        f.write_text("2020-08-31,4\nBROKEN\n2014-06-09,7\n")

        rows = parse_splits(f)

        assert len(rows) == 2

    def test_empty_file_returns_empty(self, tmp_path):
        """An empty file yields an empty list."""
        f = tmp_path / "AAPL.txt"
        f.write_text("")

        assert parse_splits(f) == []

    def test_duplicate_effective_date_deduplicated_last_wins(self, tmp_path):
        """A repeated effective_date yields a single row (last wins)."""
        f = tmp_path / "AAPL.txt"
        f.write_text("2020-08-31,4\n2014-06-09,7\n2020-08-31,4\n")

        rows = parse_splits(f)

        eff_dates = [r.effective_date for r in rows]
        assert eff_dates.count(date(2020, 8, 31)) == 1
        assert len(rows) == 2

    @pytest.mark.parametrize("bad", ["nan", "inf", "0", "-2"])
    def test_non_finite_or_non_positive_ratio_skipped(self, tmp_path, bad):
        """A split ratio must be a finite positive Decimal; reject nan/inf/<=0."""
        f = tmp_path / "AAPL.txt"
        f.write_text(f"2020-08-31,{bad}\n2014-06-09,7\n")

        rows = parse_splits(f)

        assert len(rows) == 1
        assert rows[0].effective_date == date(2014, 6, 9)
