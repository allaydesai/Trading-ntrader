"""Unit tests for the `metadata` CLI group (Story 3.2 unresolved-venue report)."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from src.cli.commands.metadata import _render_unresolved, metadata
from src.db.exceptions import DatabaseConnectionError
from src.db.models.catalog_instrument import CatalogInstrument
from src.db.models.instrument_metadata import InstrumentMetadata
from src.models.instrument_metadata import NA_SENTINEL, AssetType, ResolutionStatus
from src.services.metadata.qualification_sync import QualificationSyncResult


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


@pytest.fixture(autouse=True)
def stub_catalog_repo():
    """Neutral catalog repository for every test in this module.

    ``apply-overrides`` and ``coverage`` both reach into catalog_instruments now
    (for qualification sync and the gate's qualification term). Defaults are the
    healthy case — no gap, no rows — so tests that are not about qualification stay
    focused; the ones that are override these explicitly.
    """
    with patch("src.cli.commands.metadata.SyncCatalogInstrumentRepository") as cat:
        cat.return_value.count_unqualified_resolved.return_value = 0
        cat.return_value.iter_by_catalog.return_value = iter(())
        yield cat


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
        # Real strings, not MagicMocks: these reach Path() and a SQL parameter.
        settings.firstrate.firstrate_venue_exclusions_path = "venue_exclusions.csv"
        settings.firstrate.firstrate_catalog_name = "firstrate-etf"
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

    def test_empty_file_with_sync_disabled_is_noop(self, runner):
        """Nothing to apply and no sync requested — short-circuit before touching the DB."""
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={}),
        ):
            result = runner.invoke(metadata, ["apply-overrides", "--no-sync-catalog"])

        assert result.exit_code == 0
        assert "No venue decisions to apply" in result.output

    def test_empty_file_still_syncs_qualification_by_default(self, runner):
        """An empty override file must not skip the sync.

        Qualification drift has causes other than a new override — a prior run that
        died before syncing, or an import that never qualified. The sweep is
        convergent, so running it on an empty register is exactly how that drift
        gets healed.
        """
        session_patch, repo_patch = _patch_session_and_repo()
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={}),
            patch(
                "src.cli.commands.metadata.sync_resolved_qualifications",
                return_value=QualificationSyncResult(synced=7, cleared=1, unchanged=3),
            ) as mock_sync,
            session_patch,
            repo_patch,
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        mock_sync.assert_called_once()
        assert "7 synced" in result.output
        assert "1 cleared" in result.output

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

    def test_no_sync_catalog_skips_the_sweep(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={"SPY": "ARCA"}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={}),
            patch("src.cli.commands.metadata.merge_venue_overrides"),
            patch("src.cli.commands.metadata.sync_resolved_qualifications") as mock_sync,
            session_patch,
            repo_patch,
        ):
            result = runner.invoke(metadata, ["apply-overrides", "--no-sync-catalog"])

        assert result.exit_code == 0
        mock_sync.assert_not_called()

    def test_catalog_option_is_threaded_into_the_sync(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={}),
            patch(
                "src.cli.commands.metadata.sync_resolved_qualifications",
                return_value=QualificationSyncResult(),
            ) as mock_sync,
            session_patch,
            repo_patch,
        ):
            runner.invoke(metadata, ["apply-overrides", "--catalog", "firstrate-stocks"])

        assert mock_sync.call_args.kwargs["catalog_name"] == "firstrate-stocks"

    def test_exclusions_are_applied_and_summarised(self, runner):
        from src.services.metadata.venue_exclusions import (
            ExclusionRecord,
            VenueExclusionMergeResult,
        )

        session_patch, repo_patch = _patch_session_and_repo()
        record = ExclusionRecord(ticker="ZZZZ", reason="delisted", evidence="url")
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={"ZZZZ": record}),
            patch(
                "src.cli.commands.metadata.merge_venue_exclusions",
                return_value=VenueExclusionMergeResult(applied=1),
            ),
            patch(
                "src.cli.commands.metadata.sync_resolved_qualifications",
                return_value=QualificationSyncResult(),
            ),
            session_patch,
            repo_patch,
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert "Venue exclusions: 1 applied" in result.output

    def test_no_exclusions_flag_skips_the_register(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
            patch("src.cli.commands.metadata.load_venue_exclusions") as mock_load,
            patch(
                "src.cli.commands.metadata.sync_resolved_qualifications",
                return_value=QualificationSyncResult(),
            ),
            session_patch,
            repo_patch,
        ):
            runner.invoke(metadata, ["apply-overrides", "--no-exclusions"])

        mock_load.assert_not_called()

    def test_override_shadowing_an_exclusion_is_reported(self, runner):
        """A ticker in both registers is contradictory — the override wins, loudly."""
        from src.services.metadata.venue_exclusions import ExclusionRecord

        session_patch, repo_patch = _patch_session_and_repo()
        record = ExclusionRecord(ticker="SPY", reason="delisted", evidence="url")
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={"SPY": "ARCA"}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={"SPY": record}),
            patch("src.cli.commands.metadata.merge_venue_overrides"),
            patch(
                "src.cli.commands.metadata.sync_resolved_qualifications",
                return_value=QualificationSyncResult(),
            ),
            session_patch,
            repo_patch,
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert "overridden by a resolved venue" in result.output
        assert "SPY" in result.output

    def test_malformed_exclusion_file_degrades_gracefully(self, runner):
        from src.services.metadata.venue_exclusions import VenueExclusionError

        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={}),
            patch(
                "src.cli.commands.metadata.load_venue_exclusions",
                side_effect=VenueExclusionError("header must be exactly 'ticker,reason,evidence'"),
            ),
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "not applied" in result.output

    def test_sync_failure_aborts_the_whole_transaction(self, runner):
        """The override merge must not survive a failed sync.

        Committing the venue without the identity is precisely the half-applied
        state that produces a green gate over an unloadable universe.
        """
        session = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = False
        with (
            self._patch_settings(),
            patch("src.cli.commands.metadata.load_venue_overrides", return_value={"SPY": "ARCA"}),
            patch("src.cli.commands.metadata.load_venue_exclusions", return_value={}),
            patch("src.cli.commands.metadata.merge_venue_overrides"),
            patch(
                "src.cli.commands.metadata.sync_resolved_qualifications",
                side_effect=SQLAlchemyError("boom"),
            ),
            patch("src.cli.commands.metadata.get_sync_session", return_value=ctx),
            patch("src.cli.commands.metadata.SyncInstrumentMetadataRepository"),
        ):
            result = runner.invoke(metadata, ["apply-overrides"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output
        session.commit.assert_not_called()


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
class TestCoverageQualificationTerm:
    """The gate's second term: a resolved universe that is actually loadable."""

    def test_qualification_gap_fails_the_gate_despite_full_venue_coverage(
        self, runner, stub_catalog_repo
    ):
        """The exact pre-fix defect: 100% venue coverage, nothing loadable."""
        session_patch, repo_patch = _patch_session_and_repo()
        stub_catalog_repo.return_value.count_unqualified_resolved.return_value = 3451
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {ResolutionStatus.RESOLVED: 4612}
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 1
        assert "COMPLETE" in result.output  # venue term passes...
        assert "UNQUALIFIED" in result.output  # ...qualification term does not
        assert "3451 of 4612" in result.output

    def test_both_terms_clean_passes(self, runner, stub_catalog_repo):
        session_patch, repo_patch = _patch_session_and_repo()
        stub_catalog_repo.return_value.count_unqualified_resolved.return_value = 0
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {ResolutionStatus.RESOLVED: 10}
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 0
        assert "gate PASS" in result.output
        assert "QUALIFIED" in result.output

    def test_excluded_rows_are_surfaced_not_hidden(self, runner):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {
                ResolutionStatus.RESOLVED: 8,
                ResolutionStatus.EXCLUDED: 2,
            }
            result = runner.invoke(metadata, ["coverage", "--gate"])

        assert result.exit_code == 0
        assert "Excluded (EXCLUDED): 2" in result.output
        assert "venue_exclusions.csv" in result.output

    def test_catalog_option_is_threaded_through(self, runner, stub_catalog_repo):
        session_patch, repo_patch = _patch_session_and_repo()
        with session_patch, repo_patch as mock_repo:
            mock_repo.return_value.count_by_status.return_value = {ResolutionStatus.RESOLVED: 1}
            result = runner.invoke(metadata, ["coverage", "--catalog", "firstrate-stocks"])

        assert result.exit_code == 0
        stub_catalog_repo.return_value.count_unqualified_resolved.assert_called_once_with(
            "firstrate-stocks"
        )


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
        assert "backtestable" in cli.commands["metadata"].commands


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


def _catalog_instrument(ticker: str, minute_bars: int) -> CatalogInstrument:
    return CatalogInstrument(
        ticker=ticker,
        asset_class="ETF",
        catalog_name="firstrate-etf",
        nautilus_id=f"{ticker}.NYSE",
        exchange="NYSE",
        bar_count_minute=minute_bars,
    )


@pytest.mark.unit
class TestMetadataBacktestable:
    """`metadata backtestable` report (Story 3.5)."""

    def _patch_both_repos(self):
        session_patch, meta_patch = _patch_session_and_repo()
        cat_patch = patch("src.cli.commands.metadata.SyncCatalogInstrumentRepository")
        return session_patch, meta_patch, cat_patch

    def test_flagged_lists_tickers_and_retained_bars(self, runner):
        session_patch, meta_patch, cat_patch = self._patch_both_repos()
        with session_patch, meta_patch as mock_meta, cat_patch as mock_cat:
            mock_meta.return_value.count_by_status.return_value = {
                ResolutionStatus.RESOLVED: 3,
                ResolutionStatus.VENUE_UNRESOLVED: 1,
            }
            mock_meta.return_value.list_by_status.return_value = [_degraded_row("ZZZ")]
            mock_cat.return_value.get_by_ticker.return_value = _catalog_instrument("ZZZ", 100)
            result = runner.invoke(metadata, ["backtestable"])

        assert result.exit_code == 0
        assert "ZZZ" in result.output
        assert "100" in result.output
        assert "Non-backtestable (venue unresolved): 1" in result.output
        assert "bars retained" in result.output
        assert "venue_overrides.csv" in result.output

    def test_all_resolved_shows_all_clear(self, runner):
        session_patch, meta_patch, cat_patch = self._patch_both_repos()
        with session_patch, meta_patch as mock_meta, cat_patch:
            mock_meta.return_value.count_by_status.return_value = {ResolutionStatus.RESOLVED: 5}
            mock_meta.return_value.list_by_status.return_value = []
            result = runner.invoke(metadata, ["backtestable"])

        assert result.exit_code == 0
        assert "0 non-backtestable" in result.output
        assert "backtestable universe = 5" in result.output

    def test_never_attempted_surfaced_not_hidden_by_all_clear(self, runner):
        """UNRESOLVED (never-attempted) rows are reported, not masked by a green banner."""
        session_patch, meta_patch, cat_patch = self._patch_both_repos()
        with session_patch, meta_patch as mock_meta, cat_patch:
            mock_meta.return_value.count_by_status.return_value = {
                ResolutionStatus.RESOLVED: 2,
                ResolutionStatus.UNRESOLVED: 3,
            }
            mock_meta.return_value.list_by_status.return_value = []
            result = runner.invoke(metadata, ["backtestable"])

        assert result.exit_code == 0
        assert "All venue-resolved" not in result.output  # not over-claimed
        assert "Not yet attempted (UNRESOLVED): 3" in result.output

    def test_empty_db_reports_no_rows(self, runner):
        session_patch, meta_patch, cat_patch = self._patch_both_repos()
        with session_patch, meta_patch as mock_meta, cat_patch:
            mock_meta.return_value.count_by_status.return_value = {}
            mock_meta.return_value.list_by_status.return_value = []
            result = runner.invoke(metadata, ["backtestable"])

        assert result.exit_code == 0
        assert "No metadata rows yet" in result.output

    def test_flagged_ticker_without_catalog_row_reports_zero_bars(self, runner):
        session_patch, meta_patch, cat_patch = self._patch_both_repos()
        with session_patch, meta_patch as mock_meta, cat_patch as mock_cat:
            mock_meta.return_value.count_by_status.return_value = {
                ResolutionStatus.VENUE_UNRESOLVED: 1
            }
            mock_meta.return_value.list_by_status.return_value = [_degraded_row("ZZZ")]
            mock_cat.return_value.get_by_ticker.return_value = None
            result = runner.invoke(metadata, ["backtestable"])

        assert result.exit_code == 0
        assert "ZZZ" in result.output
        assert "Non-backtestable (venue unresolved): 1" in result.output

    def test_catalog_option_overrides_default(self, runner):
        session_patch, meta_patch, cat_patch = self._patch_both_repos()
        with session_patch, meta_patch as mock_meta, cat_patch as mock_cat:
            mock_meta.return_value.count_by_status.return_value = {
                ResolutionStatus.VENUE_UNRESOLVED: 1
            }
            mock_meta.return_value.list_by_status.return_value = [_degraded_row("ZZZ")]
            mock_cat.return_value.get_by_ticker.return_value = _catalog_instrument("ZZZ", 10)
            result = runner.invoke(metadata, ["backtestable", "--catalog", "custom-cat"])

        assert result.exit_code == 0
        mock_cat.return_value.get_by_ticker.assert_called_once_with("custom-cat", "ZZZ")

    def test_db_unconfigured_degrades_gracefully(self, runner):
        ctx = MagicMock()
        ctx.__enter__.side_effect = RuntimeError("Database not configured")
        with patch("src.cli.commands.metadata.get_sync_session", return_value=ctx):
            result = runner.invoke(metadata, ["backtestable"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Metadata DB not available" in result.output


@pytest.mark.unit
class TestRenderBacktestable:
    """Direct render-helper tests (no CliRunner)."""

    def test_render_flagged_shows_bars_and_summary(self, capsys):
        from src.cli.commands.metadata import _render_backtestable
        from src.services.metadata.backtestable import NonBacktestableTicker

        _render_backtestable("firstrate-etf", 3, 0, [NonBacktestableTicker("ZZZ", 100)])
        out = capsys.readouterr().out
        assert "ZZZ" in out
        assert "100" in out
        assert "Non-backtestable (venue unresolved): 1" in out
        assert "Backtestable: 3" in out

    def test_render_all_clear_when_no_flagged_and_none_unattempted(self, capsys):
        from src.cli.commands.metadata import _render_backtestable

        _render_backtestable("firstrate-etf", 7, 0, [])
        out = capsys.readouterr().out
        assert "0 non-backtestable" in out
        assert "7" in out

    def test_render_surfaces_never_attempted(self, capsys):
        from src.cli.commands.metadata import _render_backtestable

        _render_backtestable("firstrate-etf", 2, 3, [])
        out = capsys.readouterr().out
        assert "All venue-resolved" not in out
        assert "Not yet attempted (UNRESOLVED): 3" in out

    def test_render_empty_reports_no_rows(self, capsys):
        from src.cli.commands.metadata import _render_backtestable

        _render_backtestable("firstrate-etf", 0, 0, [])
        out = capsys.readouterr().out
        assert "No metadata rows yet" in out
