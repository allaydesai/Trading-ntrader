"""Unit tests for the `metadata` CLI group (Story 3.2 unresolved-venue report)."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from src.cli.commands.metadata import _render_unresolved, metadata
from src.db.exceptions import DatabaseConnectionError
from src.db.models.instrument_metadata import InstrumentMetadata
from src.models.instrument_metadata import NA_SENTINEL, AssetType, ResolutionStatus


@pytest.fixture
def runner():
    return CliRunner()


def _degraded_row(ticker: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider="FMP",
        venue=None,
        asset_type=None,
        company_name=NA_SENTINEL,
        resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
    )


def _found_row(ticker: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider="FMP",
        venue=None,
        asset_type=AssetType.ETF.value,
        company_name="Example ETF",
        resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
    )


def _patch_session_and_repo():
    """Return (session, repo) patchers backed by a fake sync session."""
    ctx = MagicMock()
    ctx.__enter__.return_value = MagicMock()
    ctx.__exit__.return_value = False
    session_patch = patch("src.cli.commands.metadata.get_sync_session", return_value=ctx)
    repo_patch = patch("src.cli.commands.metadata.SyncInstrumentMetadataRepository")
    return session_patch, repo_patch


@pytest.mark.unit
class TestMetadataUnresolved:
    """`metadata unresolved` CLI subcommand."""

    def test_populated_lists_tickers_reasons_and_hint(self, runner):
        rows = [_degraded_row("ZZZ"), _found_row("SPY")]
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.list_by_status.return_value = rows
            result = runner.invoke(metadata, ["unresolved"])

        assert result.exit_code == 0
        assert "ZZZ" in result.output
        assert "SPY" in result.output
        assert "unknown to FMP" in result.output
        assert "ambiguous" in result.output
        assert "2 ticker" in result.output
        assert "venue_overrides.csv" in result.output

    def test_empty_prints_all_resolved_message(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.list_by_status.return_value = []
            result = runner.invoke(metadata, ["unresolved"])

        assert result.exit_code == 0
        assert "All venues resolved" in result.output
        assert "Ticker" not in result.output  # no table header rendered

    def test_db_unconfigured_degrades_gracefully(self, runner):
        ctx = MagicMock()
        ctx.__enter__.side_effect = RuntimeError(
            "Database not configured. Check DATABASE_URL in .env file"
        )
        with patch("src.cli.commands.metadata.get_sync_session", return_value=ctx):
            result = runner.invoke(metadata, ["unresolved"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output  # the warning branch, not a traceback

    def test_db_query_error_degrades_gracefully(self, runner):
        """A query-time DB failure (Postgres down / table not migrated) → warning, not traceback."""
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.list_by_status.side_effect = OperationalError(
                "SELECT ...", {}, Exception("connection refused")
            )
            result = runner.invoke(metadata, ["unresolved"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output

    def test_db_connection_error_degrades_gracefully(self, runner):
        """The translated DatabaseConnectionError arm is reachable and handled."""
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.list_by_status.side_effect = DatabaseConnectionError(
                "Database connection failed"
            )
            result = runner.invoke(metadata, ["unresolved"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output


@pytest.mark.unit
class TestRegistration:
    """The `metadata` group is wired into the top-level CLI."""

    def test_metadata_group_registered_with_unresolved(self):
        from src.cli.main import cli

        assert "metadata" in cli.commands
        assert "unresolved" in cli.commands["metadata"].commands


@pytest.mark.unit
class TestRenderUnresolved:
    """Direct render-helper tests (no CliRunner)."""

    def test_render_preserves_row_order_and_shows_reasons(self, capsys):
        # Rows arrive pre-ordered by the repo; the renderer must not reshuffle them.
        _render_unresolved([_found_row("SPY"), _degraded_row("ZZZ")])
        out = capsys.readouterr().out
        assert out.index("SPY") < out.index("ZZZ")
        assert "unknown to FMP" in out
        assert "unmapped" in out

    def test_render_escapes_markup_in_ticker(self, capsys):
        # A stray Rich metacharacter in a DB-sourced cell must not raise or mis-render.
        row = _degraded_row("A[B")
        _render_unresolved([row])
        out = capsys.readouterr().out
        assert "A[B" in out

    def test_render_empty(self, capsys):
        _render_unresolved([])
        out = capsys.readouterr().out
        assert "All venues resolved" in out
