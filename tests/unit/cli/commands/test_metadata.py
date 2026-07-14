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
class TestMetadataApplyOverrides:
    """`metadata apply-overrides` refresh subcommand (Story 3.3)."""

    def _patch_settings(self, path: str = "venue_overrides.csv"):
        settings = MagicMock()
        settings.firstrate.firstrate_venue_overrides_path = path
        return patch("src.cli.commands.metadata.get_settings", return_value=settings)

    def test_populated_merges_and_prints_summary(self, runner):
        from src.services.metadata.venue_overrides import VenueOverrideMergeResult

        session_patch, repo_patch = _patch_session_and_repo()
        with (
            self._patch_settings(),
            patch(
                "src.cli.commands.metadata.load_venue_overrides",
                return_value={"SPY": "ARCA"},
            ),
            patch(
                "src.cli.commands.metadata.merge_venue_overrides",
                return_value=VenueOverrideMergeResult(applied=1, unchanged=0, unmatched=["ZZZ"]),
            ),
            session_patch,
            repo_patch,
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert "1 applied" in result.output
        assert "ZZZ" in result.output  # unmatched surfaced

    def test_empty_file_is_noop(self, runner):
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert "No venue overrides to apply" in result.output

    def test_malformed_file_degrades_gracefully(self, runner):
        from src.services.metadata.venue_overrides import VenueOverrideError

        with (
            self._patch_settings(),
            patch(
                "src.cli.commands.metadata.load_venue_overrides",
                side_effect=VenueOverrideError("header must be exactly 'ticker,venue'"),
            ),
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "not applied" in result.output

    def test_db_unconfigured_degrades_gracefully(self, runner):
        ctx = MagicMock()
        ctx.__enter__.side_effect = RuntimeError("Database not configured")
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={"SPY": "ARCA"}),
            patch("src.cli.commands.metadata.get_sync_session", return_value=ctx),
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output


@pytest.mark.unit
class TestMetadataCoverage:
    """`metadata coverage` report + completeness gate (Story 3.4)."""

    def test_complete_no_flag_reports_pass(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {ResolutionStatus.RESOLVED: 3}
            result = runner.invoke(metadata, ["coverage"])

        assert result.exit_code == 0
        assert "100.0%" in result.output
        assert "COMPLETE" in result.output
        assert "gate PASS" in result.output

    def test_incomplete_no_flag_reports_fail_but_exits_zero(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {
                ResolutionStatus.RESOLVED: 2,
                ResolutionStatus.VENUE_UNRESOLVED: 1,
            }
            result = runner.invoke(metadata, ["coverage"])

        assert result.exit_code == 0  # report-only without --gate
        assert "INCOMPLETE" in result.output
        assert "gate FAIL" in result.output
        assert "66.7%" in result.output
        assert "1 ticker(s) VENUE_UNRESOLVED" in result.output

    def test_incomplete_gate_exits_one(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {
                ResolutionStatus.RESOLVED: 2,
                ResolutionStatus.VENUE_UNRESOLVED: 1,
            }
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 1  # AC2 — scriptable FAIL
        assert "INCOMPLETE" in result.output

    def test_complete_gate_exits_zero(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {ResolutionStatus.RESOLVED: 3}
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 0  # AC3
        assert "gate PASS" in result.output

    def test_db_unconfigured_no_flag_degrades_gracefully(self, runner):
        ctx = MagicMock()
        ctx.__enter__.side_effect = RuntimeError("Database not configured")
        with patch("src.cli.commands.metadata.get_sync_session", return_value=ctx):
            result = runner.invoke(metadata, ["coverage"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output

    def test_db_unconfigured_gate_exits_two(self, runner):
        """Under --gate a DB that cannot be read must NOT be treated as passing (AC4)."""
        ctx = MagicMock()
        ctx.__enter__.side_effect = RuntimeError("Database not configured")
        with patch("src.cli.commands.metadata.get_sync_session", return_value=ctx):
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 2  # could-not-evaluate ≠ pass
        assert "Metadata DB not available" in result.output

    def test_db_query_error_gate_exits_two(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.side_effect = OperationalError(
                "SELECT ...", {}, Exception("connection refused")
            )
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 2
        assert "Metadata DB not available" in result.output


@pytest.mark.unit
class TestRenderCoverage:
    """Direct render-helper tests (no CliRunner)."""

    def test_render_incomplete_shows_counts_pct_and_fail(self, capsys):
        from src.cli.commands.metadata import _render_coverage
        from src.services.metadata.coverage_report import VenueCoverage

        cov = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 2, ResolutionStatus.VENUE_UNRESOLVED: 1}
        )
        _render_coverage(cov)
        out = capsys.readouterr().out
        assert "66.7%" in out
        assert "INCOMPLETE" in out
        assert "gate FAIL" in out

    def test_render_complete_shows_pass(self, capsys):
        from src.cli.commands.metadata import _render_coverage
        from src.services.metadata.coverage_report import VenueCoverage

        _render_coverage(VenueCoverage.from_counts({ResolutionStatus.RESOLVED: 4}))
        out = capsys.readouterr().out
        assert "100.0%" in out
        assert "COMPLETE" in out
        assert "gate PASS" in out

    def test_render_empty_shows_no_rows_note(self, capsys):
        from src.cli.commands.metadata import _render_coverage
        from src.services.metadata.coverage_report import VenueCoverage

        _render_coverage(VenueCoverage.from_counts({}))
        out = capsys.readouterr().out
        assert "no metadata rows" in out.lower()

    def test_render_near_complete_never_displays_100pct_while_fail(self, capsys):
        # Review patch: 9999/10000 rounds to "100.0%" under .1f — the displayed
        # percent must be clamped so it can't contradict the FAIL verdict.
        from src.cli.commands.metadata import _render_coverage
        from src.services.metadata.coverage_report import VenueCoverage

        cov = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 9999, ResolutionStatus.VENUE_UNRESOLVED: 1}
        )
        _render_coverage(cov)
        out = capsys.readouterr().out
        assert "100.0%" not in out
        assert "99.9%" in out
        assert "gate FAIL" in out

    def test_render_only_unattempted_flags_vacuous_pass(self, capsys):
        # Review patch: decided==0 but total>0 (resolution never ran) must carry
        # the vacuous-PASS caveat, not an un-caveated green COMPLETE.
        from src.cli.commands.metadata import _render_coverage
        from src.services.metadata.coverage_report import VenueCoverage

        _render_coverage(VenueCoverage.from_counts({ResolutionStatus.UNRESOLVED: 5}))
        out = capsys.readouterr().out
        assert "vacuous" in out.lower()
        assert "resolution has not run" in out.lower()
        assert "COMPLETE" in out


@pytest.mark.unit
class TestRegistration:
    """The `metadata` group is wired into the top-level CLI."""

    def test_metadata_group_registered_with_unresolved(self):
        from src.cli.main import cli

        assert "metadata" in cli.commands
        assert "unresolved" in cli.commands["metadata"].commands
        assert "apply-overrides" in cli.commands["metadata"].commands
        assert "coverage" in cli.commands["metadata"].commands


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
