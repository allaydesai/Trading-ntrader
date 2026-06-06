"""Unit tests for SupplementaryDataLoader with repo test doubles."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.firstrate.supplementary_loader import (
    SupplementaryDataLoader,
    SupplementaryLoadResult,
)

CATALOG = "firstrate-stock"


@pytest.fixture
def mock_div_repo():
    return MagicMock()


@pytest.fixture
def mock_split_repo():
    return MagicMock()


@pytest.fixture
def loader(mock_div_repo, mock_split_repo):
    return SupplementaryDataLoader(dividend_repo=mock_div_repo, split_repo=mock_split_repo)


@pytest.mark.unit
class TestLoadForTicker:
    """Tests for SupplementaryDataLoader.load_for_ticker."""

    def test_loads_both_files(self, loader, mock_div_repo, mock_split_repo, tmp_path):
        """Both dividend and split files present → both repos replaced."""
        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()
        (div_dir / "AAPL_divs.txt").write_text("2026-02-09,0.26\n2025-08-11,0.26\n")
        (split_dir / "AAPL.txt").write_text("2020-08-31,4\n")

        result = loader.load_for_ticker("AAPL", CATALOG, div_dir, split_dir)

        assert isinstance(result, SupplementaryLoadResult)
        assert result.status == "success"
        assert result.dividend_count == 2
        assert result.split_count == 1
        mock_div_repo.replace_for_ticker.assert_called_once()
        mock_split_repo.replace_for_ticker.assert_called_once()
        # Confirm catalog + ticker passed through.
        div_args = mock_div_repo.replace_for_ticker.call_args
        assert div_args.args[0] == CATALOG
        assert div_args.args[1] == "AAPL"
        assert len(div_args.args[2]) == 2

    def test_missing_div_present_split(self, loader, mock_div_repo, mock_split_repo, tmp_path):
        """Missing dividend file is a no-op; split still loads."""
        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()
        (split_dir / "AAPL.txt").write_text("2020-08-31,4\n")

        result = loader.load_for_ticker("AAPL", CATALOG, div_dir, split_dir)

        assert result.status == "success"
        assert result.dividend_count == 0
        assert result.split_count == 1
        mock_div_repo.replace_for_ticker.assert_not_called()
        mock_split_repo.replace_for_ticker.assert_called_once()

    def test_both_missing_clean_noop(self, loader, mock_div_repo, mock_split_repo, tmp_path):
        """Neither file present → clean no-op, success, no repo writes (AC-4)."""
        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()

        result = loader.load_for_ticker("AAPL", CATALOG, div_dir, split_dir)

        assert result.status == "success"
        assert result.dividend_count == 0
        assert result.split_count == 0
        mock_div_repo.replace_for_ticker.assert_not_called()
        mock_split_repo.replace_for_ticker.assert_not_called()

    def test_none_dirs_clean_noop(self, loader, mock_div_repo, mock_split_repo):
        """Both dirs None → clean no-op success (AC-6)."""
        result = loader.load_for_ticker("AAPL", CATALOG, None, None)

        assert result.status == "success"
        assert result.dividend_count == 0
        assert result.split_count == 0
        mock_div_repo.replace_for_ticker.assert_not_called()
        mock_split_repo.replace_for_ticker.assert_not_called()

    def test_parser_failure_isolated(self, loader, mock_div_repo, mock_split_repo, tmp_path):
        """A parser raising is caught → result flagged failed, no exception escapes (AC-3)."""
        div_dir = tmp_path / "divs"
        div_dir.mkdir()
        (div_dir / "AAPL_divs.txt").write_text("2026-02-09,0.26\n")

        with patch(
            "src.services.firstrate.supplementary_loader.parse_dividends",
            side_effect=RuntimeError("boom"),
        ):
            result = loader.load_for_ticker("AAPL", CATALOG, div_dir, None)

        assert result.status == "failed"
        assert result.error is not None
        assert "boom" in result.error

    def test_present_but_empty_file_preserves_existing(
        self, loader, mock_div_repo, mock_split_repo, tmp_path
    ):
        """A present-but-empty file must NOT wipe existing rows (no empty replace)."""
        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()
        # Both files exist but yield zero valid rows (empty / all-malformed).
        (div_dir / "AAPL_divs.txt").write_text("")
        (split_dir / "AAPL.txt").write_text("GARBAGE\n\n")

        result = loader.load_for_ticker("AAPL", CATALOG, div_dir, split_dir)

        assert result.status == "success"
        assert result.dividend_count == 0
        assert result.split_count == 0
        # Crucially: replace is NOT called, so existing history is preserved.
        mock_div_repo.replace_for_ticker.assert_not_called()
        mock_split_repo.replace_for_ticker.assert_not_called()

    def test_repo_failure_isolated(self, loader, mock_div_repo, mock_split_repo, tmp_path):
        """A repo raising is caught → result flagged failed, no exception escapes."""
        div_dir = tmp_path / "divs"
        div_dir.mkdir()
        (div_dir / "AAPL_divs.txt").write_text("2026-02-09,0.26\n")
        mock_div_repo.replace_for_ticker.side_effect = RuntimeError("db down")

        result = loader.load_for_ticker("AAPL", CATALOG, div_dir, None)

        assert result.status == "failed"
        assert "db down" in result.error


@pytest.mark.unit
class TestLoadForTickers:
    """Tests for the batch entrypoint."""

    def test_aggregates_results(self, loader, tmp_path):
        """load_for_tickers iterates and returns one result per ticker."""
        div_dir = tmp_path / "divs"
        split_dir = tmp_path / "splits"
        div_dir.mkdir()
        split_dir.mkdir()
        (div_dir / "AAPL_divs.txt").write_text("2026-02-09,0.26\n")
        (split_dir / "AAPL.txt").write_text("2020-08-31,4\n")
        (div_dir / "MSFT_divs.txt").write_text("2026-03-01,0.75\n")

        results = loader.load_for_tickers(["AAPL", "MSFT"], CATALOG, div_dir, split_dir)

        assert len(results) == 2
        by_ticker = {r.ticker: r for r in results}
        assert by_ticker["AAPL"].split_count == 1
        assert by_ticker["MSFT"].dividend_count == 1
        assert by_ticker["MSFT"].split_count == 0

    def test_one_failure_does_not_abort_batch(self, loader, mock_div_repo, tmp_path):
        """One ticker failing leaves the others processed (AC-3 isolation)."""
        div_dir = tmp_path / "divs"
        div_dir.mkdir()
        (div_dir / "AAPL_divs.txt").write_text("2026-02-09,0.26\n")
        (div_dir / "MSFT_divs.txt").write_text("2026-03-01,0.75\n")

        # Fail only on AAPL's replace.
        def _maybe_fail(catalog, ticker, rows):
            if ticker == "AAPL":
                raise RuntimeError("boom")

        mock_div_repo.replace_for_ticker.side_effect = _maybe_fail

        results = loader.load_for_tickers(["AAPL", "MSFT"], CATALOG, div_dir, None)

        by_ticker = {r.ticker: r for r in results}
        assert by_ticker["AAPL"].status == "failed"
        assert by_ticker["MSFT"].status == "success"
        assert by_ticker["MSFT"].dividend_count == 1
