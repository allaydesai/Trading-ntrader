"""Unit tests for `ntrader live status`/`live list` (Story 2.8).

Every test patches ``get_sync_session``/``SyncTradingSessionRepository`` at
their **new-module** targets — ``src.cli.commands.live_status``, not
``src.cli.commands.live`` — so nothing here opens a real database connection.
``SessionService`` itself is exercised for real against the mocked
repository: it is pure orchestration over the injected repo, so mocking only
the repository proves the CLI's wiring rather than assuming it.
"""

import ast
import io
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner
from rich.console import Console

from src.cli.commands import live_status
from src.cli.commands.live import live
from src.core.live_check import EXIT_ERROR
from src.core.live_trader_id import derive_trader_id
from src.db.exceptions import RecordNotFoundError
from src.models.session import SessionStatus
from src.services.session_service import DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS

pytestmark = pytest.mark.unit

_STATUS_GET_SYNC_SESSION = "src.cli.commands.live_status.get_sync_session"
_STATUS_SESSION_REPO = "src.cli.commands.live_status.SyncTradingSessionRepository"

NOW = datetime.now(timezone.utc)
SESSION_ID = UUID("44444444-4444-4444-4444-444444444444")

#: The read-only surface `status`/`list` are allowed to touch. Anything else
#: called on the repository stand-in is a write this story must never make.
_READ_ONLY_REPO_METHODS = frozenset(
    {"find_by_name", "find_by_session_id", "find_all", "trade_counts_by_session"}
)


def _module_scope_get_settings_calls(source: str) -> list[str]:
    """`get_settings()` calls that execute at **import** time.

    Top-level `def`/`class` nodes are skipped entirely rather than walked:
    a call inside a function body runs when the command runs, which is the
    lazy pattern the blast-radius item asks for, not a module-scope call.
    """
    tree = ast.parse(source)
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "get_settings"
            ):
                found.append(sub.func.id)
    return found


def _ar36_violations(text: str) -> list[str]:
    """AR36's five forbidden lifecycle stems, with the trading-domain carve-out.

    `closed trade`/`closed_trade_count` are scrubbed before matching because
    AR36 bans those words *as synonyms for* the lifecycle concept, not
    globally (the Story 1.2 precedent), and "closed trade" is the term AR29
    itself mandates. Shared by the CLI and core scans so one probe covers both.
    """
    scrubbed = text.lower().replace("closed trade", "").replace("closed_trade_count", "")
    hits: list[str] = []
    for forbidden in ("pause", "halt", "kill", "close", "finalize"):
        hits.extend(re.findall(rf"\b{forbidden}\w*\b", scrubbed))
    return hits


@pytest.fixture
def runner():
    return CliRunner()


@contextmanager
def _fake_session_cm():
    yield MagicMock()


def _row(**overrides):
    """A ``TradingSession``-shaped stand-in with **every** field the reader
    branches on set explicitly — a bare ``MagicMock`` attribute is truthy,
    the standing trap this repo's other CLI test files document."""
    row = MagicMock()
    row.id = overrides.get("id", 42)
    row.session_id = overrides.get("session_id", SESSION_ID)
    row.name = overrides.get("name", "alpha-session")
    row.status = overrides.get("status", SessionStatus.RUNNING)
    row.last_heartbeat_at = overrides.get("last_heartbeat_at", NOW - timedelta(seconds=5))
    row.last_bar_at = overrides.get("last_bar_at", NOW - timedelta(seconds=5))
    row.last_started_at = overrides.get("last_started_at", NOW - timedelta(seconds=500))
    row.last_stopped_at = overrides.get("last_stopped_at", None)
    row.sealed_at = overrides.get("sealed_at", None)
    row.runtime_flags = overrides.get("runtime_flags", None)
    return row


@contextmanager
def _status_harness(*, row=None, by_name=True, counts=(0, 0), resolve_error=None):
    row = _row() if row is None else row
    repo = MagicMock()
    repo.find_by_name.return_value = row if by_name else None
    repo.find_by_session_id.return_value = None if by_name else row
    if resolve_error is not None:
        repo.find_by_name.side_effect = resolve_error
        repo.find_by_session_id.side_effect = resolve_error
        repo.find_by_name.return_value = None
        repo.find_by_session_id.return_value = None
    repo.trade_counts_by_session.return_value = {row.id: counts}
    repo_cls = MagicMock(return_value=repo)

    with (
        patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
        patch(_STATUS_SESSION_REPO, repo_cls),
    ):
        yield {"repo": repo, "row": row}


class TestGroupRegistration:
    """AC #1 — both commands are wired into the `live` group."""

    def test_live_help_lists_status(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "status" in result.output

    def test_live_help_lists_list(self, runner):
        result = runner.invoke(live, ["--help"])

        assert result.exit_code == 0
        assert "list" in result.output

    def test_status_takes_exactly_a_session_and_a_json_flag(self):
        from src.cli.commands.live_status import status

        names = {param.name for param in status.params}
        assert names == {"session", "as_json"}

    def test_the_session_is_a_positional_argument(self):
        import click

        from src.cli.commands.live_status import status

        positional = [p for p in status.params if isinstance(p, click.Argument)]
        assert [p.name for p in positional] == ["session"]

    def test_list_takes_only_a_json_flag(self):
        from src.cli.commands.live_status import list_sessions

        names = {param.name for param in list_sessions.params}
        assert names == {"as_json"}


class TestStatusResolvesFromTheDatabaseAlone:
    """AC #1: no TradingNode, no runner import, works with the runner absent."""

    def test_resolves_by_name(self, runner):
        with _status_harness(by_name=True) as spies:
            result = runner.invoke(live, ["status", "alpha-session"])

        assert result.exit_code == 0
        spies["repo"].find_by_name.assert_called_with("alpha-session")

    def test_resolves_by_session_id(self, runner):
        with _status_harness(by_name=False) as spies:
            result = runner.invoke(live, ["status", str(SESSION_ID)])

        assert result.exit_code == 0
        spies["repo"].find_by_session_id.assert_called_with(SESSION_ID)

    def test_module_imports_no_nautilus_or_runner(self):
        source = Path(live_status.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = ("nautilus_trader", "ibapi", "src.core.live_session_runner")
        offenders = [name for name in imported if name.startswith(forbidden)]
        assert offenders == []

    def test_module_has_no_module_scope_get_settings_call(self):
        """Deferred-work's blast-radius item: a settings validation error must
        not kill `--help`.

        Scopes the scan to statements that really execute at import. The
        first version walked *into* top-level `def`/`class` bodies, so it
        rejected the lazy, inside-the-command-body call the deferred-work item
        actually recommends — and passed only because the module makes no such
        call at all, leaving it vacuous in the other direction.
        """
        source = Path(live_status.__file__).read_text(encoding="utf-8")

        assert _module_scope_get_settings_calls(source) == []

    def test_the_module_scope_scan_can_actually_fail(self):
        """Non-vacuity probe: prove the scanner fires on a real offender, and
        that it does not fire on the lazy call the item permits."""
        offender = "from src.config import get_settings\nsettings = get_settings()\n"
        permitted = (
            "from src.config import get_settings\n"
            "def status():\n"
            "    settings = get_settings()\n"
            "    return settings\n"
        )

        assert _module_scope_get_settings_calls(offender) == ["get_settings"]
        assert _module_scope_get_settings_calls(permitted) == []

    def test_the_cli_passes_the_real_staleness_threshold_constant(self, runner):
        """AC #2: the 90s threshold is imported, **never a new literal**.

        The story claimed this pin existed; it did not. Verified to fail when
        either call site is changed to a literal — which previously left the
        whole unit tier green.
        """
        seen: list[float] = []
        real_builder = live_status.build_status_report

        def _spy(**kwargs):
            seen.append(kwargs["heartbeat_stale_after_seconds"])
            return real_builder(**kwargs)

        with _status_harness(), patch.object(live_status, "build_status_report", _spy):
            status_result = runner.invoke(live, ["status", "alpha-session"])

        repo = MagicMock()
        repo.find_all.return_value = [_row()]
        repo.trade_counts_by_session.return_value = {42: (0, 0)}
        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
            patch.object(live_status, "build_status_report", _spy),
        ):
            list_result = runner.invoke(live, ["list"])

        assert status_result.exit_code == 0
        assert list_result.exit_code == 0
        assert len(seen) == 2
        # Identity, not equality: a restated `90.0` literal in `live_status.py`
        # is a *different float object* from `session_service`'s, so `is`
        # catches the one mutation `==` would wave through.
        assert all(value is DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS for value in seen)

    def test_the_module_restates_no_threshold_literal(self):
        """The other half of "never a new literal": no bare 90 anywhere in the
        module, so the constant cannot be quietly inlined later."""
        source = Path(live_status.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        numbers = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
        ]
        assert DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS not in numbers
        assert 90 not in numbers


class TestStatusReportsWhatTheAcNames:
    """AC #1: state, closed-trade count, open positions, last activity, health."""

    def test_reports_state_counts_and_health(self, runner):
        """Counts are asserted as the **whole rendered line**, and health as an
        exact value.

        The first version asserted `"3" in output` and `"1" in output` with a
        `trading or idle` disjunction — which passed with the two counts
        swapped (verified by mutation: the full suite stayed green), and was
        latently flaky besides, since a UUID and an ISO timestamp put plenty
        of stray digits in the same output.
        """
        with _status_harness(counts=(3, 1)):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert result.exit_code == 0
        assert "state: running" in result.output
        assert "closed trades: 3, open positions: 1" in result.output
        assert "health: trading" in result.output

    def test_the_counts_are_not_transposed_in_the_list_table(self, runner):
        """The same swap, on `list`'s other loader — it indexes `[0]`/`[1]`
        rather than unpacking, so it needs its own pin."""
        row = _row(id=11, name="counts-session")
        repo = MagicMock()
        repo.find_all.return_value = [row]
        repo.trade_counts_by_session.return_value = {11: (5, 2)}

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list", "--json"])

        payload = json.loads(result.output)
        assert payload[0]["closed_trade_count"] == 5
        assert payload[0]["open_positions"] == 2

    def test_a_running_session_with_a_frozen_heartbeat_reports_stale(self, runner):
        """AC #3: a SIGKILLed runner leaves the row `running` with the heartbeat
        frozen — that must read `stale`, never `idle` and never `trading`.

        `stale` appeared **nowhere** in this file or the e2e file before: the
        only null-heartbeat rows were paired with `CREATED`, which
        short-circuits at precedence step 1 to `stopped`.
        """
        row = _row(
            status=SessionStatus.RUNNING,
            last_heartbeat_at=NOW - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS + 1),
            last_bar_at=NOW - timedelta(seconds=1),
        )
        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert result.exit_code == 0, "reporting bad news is still a successful report"
        assert "health: stale" in result.output

    def test_two_invocations_straddling_the_threshold_flip_health_without_a_write(self, runner):
        """AC #2's closing clause, proved at the level the AC names: **two
        `status` invocations**, a stepped clock, and no intervening write.

        The original proof called the pure `derive_health()` twice — no
        `CliRunner`, no repository, and no assertion that nothing was written,
        which left both load-bearing halves untested.
        """
        started = NOW - timedelta(seconds=600)
        heartbeat = NOW - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS - 1)
        row = _row(status=SessionStatus.RUNNING, last_heartbeat_at=heartbeat, last_bar_at=None)
        row.last_started_at = started

        with _status_harness(row=row) as spies:
            with patch.object(live_status, "_utc_now", return_value=NOW):
                first = runner.invoke(live, ["status", "alpha-session"])
            # Only the clock moves — the row object is not touched between calls.
            with patch.object(live_status, "_utc_now", return_value=NOW + timedelta(seconds=5)):
                second = runner.invoke(live, ["status", "alpha-session"])

            called = {name for name, _, _ in spies["repo"].mock_calls if name}

        assert "health: idle" in first.output
        assert "health: stale" in second.output
        assert called <= _READ_ONLY_REPO_METHODS, f"status performed a write: {called}"

    def test_a_never_started_session_reports_never(self, runner):
        row = _row(
            status=SessionStatus.CREATED,
            last_heartbeat_at=None,
            last_bar_at=None,
            last_started_at=None,
        )
        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert result.exit_code == 0
        assert "never" in result.output

    def test_the_trader_id_renders_in_human_output(self, runner):
        with _status_harness() as spies:
            result = runner.invoke(live, ["status", "alpha-session"])

        assert derive_trader_id(spies["row"].session_id) in result.output

    def test_an_unknown_session_exits_one_with_the_prefix(self, runner):
        with _status_harness(resolve_error=RecordNotFoundError("No trading session matches 'x'")):
            result = runner.invoke(live, ["status", "x"])

        assert result.exit_code == EXIT_ERROR
        assert "live status failed" in result.output


class TestFailurePathsNameTheRightCommand:
    """The shared exit path must not attribute a failure to another command."""

    @staticmethod
    def _failing_repo_cls(exc):
        repo = MagicMock()
        repo.find_all.side_effect = exc
        repo.find_by_name.side_effect = exc
        repo.find_by_session_id.side_effect = exc
        return MagicMock(return_value=repo)

    def test_a_failing_list_says_live_list_failed(self, runner):
        """`_exit_on_failure` hardcoded the `status` prefix and `list` shared
        it, so an operator whose `live list` failed was told `live status`
        had — the exact defect the Dev Notes warned against, one command over.
        There was no failure-path test for `list` at all."""
        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, self._failing_repo_cls(RuntimeError("db is down"))),
        ):
            result = runner.invoke(live, ["list"])

        assert result.exit_code == EXIT_ERROR
        assert "live list failed" in result.output
        assert "live status failed" not in result.output

    def test_a_failing_status_still_says_live_status_failed(self, runner):
        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, self._failing_repo_cls(RuntimeError("db is down"))),
        ):
            result = runner.invoke(live, ["status", "anything"])

        assert result.exit_code == EXIT_ERROR
        assert "live status failed" in result.output
        assert "live list failed" not in result.output

    @pytest.mark.parametrize("argv", [["status", "missing", "--json"], ["list", "--json"]])
    def test_json_mode_keeps_stdout_parseable_on_failure(self, runner, argv):
        """AC #7 makes stdout the machine-readable channel. A human-readable
        failure line there is what breaks `... --json | jq` on exactly the
        paths a monitoring script most needs to handle, so under `--json` the
        message goes to stderr instead."""
        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, self._failing_repo_cls(RuntimeError("db is down"))),
        ):
            result = runner.invoke(live, argv)

        assert result.exit_code == EXIT_ERROR
        assert result.stdout == "", f"--json polluted stdout with: {result.stdout!r}"
        assert "failed" in result.stderr


class TestStatusJson:
    """AC #7: exact seven keys, machine-parseable, no ANSI/log pollution."""

    EXPECTED_KEYS = {
        "session_id",
        "name",
        "status",
        "closed_trade_count",
        "open_positions",
        "last_activity_at",
        "health",
    }

    def test_json_output_is_parseable_and_has_exactly_the_pinned_keys(self, runner):
        with _status_harness(counts=(2, 0)):
            result = runner.invoke(live, ["status", "alpha-session", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload) == self.EXPECTED_KEYS

    def test_trader_id_never_leaks_into_json(self, runner):
        with _status_harness():
            result = runner.invoke(live, ["status", "alpha-session", "--json"])

        payload = json.loads(result.output)
        assert "trader_id" not in payload

    def test_timestamps_are_iso8601_utc(self, runner):
        with _status_harness():
            result = runner.invoke(live, ["status", "alpha-session", "--json"])

        payload = json.loads(result.output)
        assert payload["last_activity_at"].endswith("+00:00")

    def test_a_never_started_session_renders_null(self, runner):
        row = _row(
            status=SessionStatus.CREATED,
            last_heartbeat_at=None,
            last_bar_at=None,
            last_started_at=None,
        )
        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session", "--json"])

        payload = json.loads(result.output)
        assert payload["last_activity_at"] is None


class TestListCommand:
    """AC #6: every session, `find_all()`'s order, an empty-table line."""

    def _repo_with_sessions(self, rows, counts_by_id=None):
        repo = MagicMock()
        repo.find_all.return_value = rows
        repo.trade_counts_by_session.return_value = counts_by_id or {row.id: (0, 0) for row in rows}
        return repo

    def test_renders_every_session_in_find_all_order(self, runner):
        """Names deliberately in reverse alphabetical order from ``find_all``'s
        newest-first return, so a mutation that silently re-sorts the rows
        (e.g. alphabetically) cannot pass this test by coincidence — it did,
        once, before this fixture was corrected (Task 10, mutation #7)."""
        newer = _row(id=2, name="zzz-newer-session", session_id=uuid4())
        older = _row(id=1, name="aaa-older-session", session_id=uuid4())
        repo = self._repo_with_sessions([newer, older])

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list"])

        assert result.exit_code == 0
        assert result.output.index("zzz-newer-session") < result.output.index("aaa-older-session")

    def test_a_maximum_length_name_renders_in_full(self, runner):
        """`live create` accepts names up to MAX_SESSION_NAME_LENGTH (100), but
        the other six columns leave `Name` roughly 77 characters — and Rich's
        default crop silently produced a name that could not be pasted back
        into `ntrader live status <name>`. `overflow="fold"` wraps instead."""
        name = "s" * 91 + "ENDMARKER"
        assert len(name) == 100
        row = _row(id=5, name=name)
        repo = self._repo_with_sessions([row], counts_by_id={5: (0, 0)})

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list"])

        assert result.exit_code == 0
        assert "ENDMARKER" in result.output, "the tail of the name was cropped away"
        assert "…" not in result.output

    def test_an_empty_table_prints_a_first_party_line_and_exits_zero(self, runner):
        repo = self._repo_with_sessions([])

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list"])

        assert result.exit_code == 0
        assert "no sessions" in result.output.lower()

    def test_list_json_is_an_array_of_the_pinned_shape(self, runner):
        row = _row(id=7, name="json-list-session")
        repo = self._repo_with_sessions([row], counts_by_id={7: (1, 0)})

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert payload[0]["name"] == "json-list-session"
        assert set(payload[0]) == TestStatusJson.EXPECTED_KEYS

    def test_an_empty_list_json_is_an_empty_array(self, runner):
        repo = self._repo_with_sessions([])

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_json_mode_has_no_ansi_or_rich_pollution(self, runner):
        """Asserted against a **force_terminal** console, so the assertion can
        actually fail.

        Under a plain `CliRunner` the captured stream is not a tty, so Rich
        disables colour unconditionally and `"\\x1b[" not in output` held no
        matter what the command printed — deleting `highlight=False` left it
        green. The probe below pins that this version really does catch it.
        """
        row = _row(id=9, name="plain-json-session")
        repo = self._repo_with_sessions([row], counts_by_id={9: (0, 0)})
        buffer = io.StringIO()
        colour_console = Console(file=buffer, force_terminal=True, width=200)

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
            patch.object(live_status, "console", colour_console),
        ):
            result = runner.invoke(live, ["list", "--json"])

        emitted = buffer.getvalue()
        assert result.exit_code == 0
        assert "\x1b[" not in emitted
        assert json.loads(emitted)[0]["name"] == "plain-json-session"

    def test_the_ansi_probe_can_actually_fail(self):
        """Non-vacuity probe: the same console, printing the same JSON with
        Rich's highlighter left on, does emit ANSI — so the assertion above is
        testing `highlight=False`, not the absence of a tty."""
        buffer = io.StringIO()
        colour_console = Console(file=buffer, force_terminal=True, width=200)

        colour_console.print(json.dumps({"closed_trade_count": 3}), highlight=True)

        assert "\x1b[" in buffer.getvalue()


class TestDegradedStrategyVisibility:
    """AC #4, #5: a contained failure is visible from another process."""

    def _failure(self, spec_strategy_id="sma_crossover", **overrides):
        fields = {
            "strategy_id": "SMACrossover-000",
            "spec_strategy_id": spec_strategy_id,
            "error_type": "DivisionByZero",
            "handler": "handle_bar",
            "at": "2026-08-23T14:03:11+00:00",
            "detail": "[<class 'decimal.DivisionByZero'>]",
        }
        fields.update(overrides)
        return fields

    def test_a_failed_strategy_renders_every_named_field(self, runner):
        flags = {"all_failed": False, "failed_strategies": [self._failure()]}
        row = _row(runtime_flags=flags)

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert "sma_crossover" in result.output
        assert "SMACrossover-000" in result.output
        assert "DivisionByZero" in result.output
        assert "handle_bar" in result.output

    def test_all_failed_gets_its_own_line(self, runner):
        flags = {"all_failed": True, "failed_strategies": [self._failure()]}
        row = _row(runtime_flags=flags)

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert "no longer trade" in result.output

    def test_a_stopped_sessions_failures_still_render(self, runner):
        """`runtime_flags` is cleared only on `-> running`, so a stopped
        session's failures remain readable."""
        flags = {"all_failed": False, "failed_strategies": [self._failure()]}
        row = _row(status=SessionStatus.STOPPED, runtime_flags=flags)

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert result.exit_code == 0
        assert "sma_crossover" in result.output

    def test_failed_strategies_never_leak_into_json(self, runner):
        flags = {"all_failed": True, "failed_strategies": [self._failure()]}
        row = _row(runtime_flags=flags)

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session", "--json"])

        payload = json.loads(result.output)
        assert "failed_strategies" not in payload
        assert "all_failed" not in payload

    def test_the_wording_respects_ar36s_vocabulary(self, runner):
        """Same five-stem word-boundary scan as the `live start` CLI test,
        with the `closed trade` domain term carved out explicitly."""
        flags = {"all_failed": True, "failed_strategies": [self._failure()]}
        row = _row(runtime_flags=flags)

        with _status_harness(row=row, counts=(4, 0)):
            result = runner.invoke(live, ["status", "alpha-session"])

        hits = _ar36_violations(result.output)
        assert not hits, f"AR36 forbidden vocabulary {hits} in: {result.output}"

    def test_the_ar36_scan_can_actually_fail(self):
        """Non-vacuity probe, required by the story's own Dev Notes and missing
        from the shipped scan.

        The scan `.replace()`s the carved-out domain term before matching, and
        nothing proved that scrub was narrower than the scan — a scrub that ate
        too much would silently disarm it. Three assertions: a planted stem is
        caught, the carve-out does not disarm the same stem elsewhere, and the
        legitimate trading term is still exempt.
        """
        assert _ar36_violations("the session was halted and killed") == ["halted", "killed"]
        assert _ar36_violations("please close the session") == ["close"]
        assert _ar36_violations("closed trades: 3, open positions: 1") == []

    def test_a_connection_loss_names_its_cause(self, runner):
        """AC #4 sense (b): the dormant `connection_lost_at` reader must render
        an explanation, not a bare `health: degraded` with nothing to act on.

        The writer is Epic 4's — this pins that the *rendering* is already in
        place, so Epic 4's writer lights the whole path up with no change here.
        """
        row = _row(runtime_flags={"v": 1, "connection_lost_at": "2026-08-24T11:00:00+00:00"})

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert "health: degraded" in result.output
        assert "2026-08-24T11:00:00+00:00" in result.output
        assert "connection" in result.output.lower()

    def test_all_failed_alone_still_names_its_cause(self, runner):
        """`all_failed` is an independent degradation sense in the derivation,
        but the renderer nested its line inside the `failed_strategies` block —
        so a row with `all_failed: true` and an empty entries list reported
        `degraded` with no explanation on either output path."""
        row = _row(runtime_flags={"v": 1, "all_failed": True, "failed_strategies": []})

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert "health: degraded" in result.output
        assert "no longer trade" in result.output

    @pytest.mark.parametrize(
        "flags",
        [
            {"v": 1, "failed_strategies": ["sma_crossover"]},
            {"v": 1, "failed_strategies": "sma_crossover"},
            {"v": 1, "failed_strategies": 3},
            {"v": 1, "failed_strategies": [{"spec_strategy_id": "s1"}, "not-a-mapping"]},
            ["boom"],
            "boom",
        ],
    )
    def test_a_malformed_runtime_flags_document_never_tracebacks(self, runner, flags):
        """`runtime_flags` is unconstrained JSONB with no server-side schema, and
        rendering runs *outside* the CLI's exit-code guard — so a shape the
        single writer never produces (a hand-edited row, a restored backup, the
        next writer) raised `AttributeError`/`TypeError` straight past AR28's
        table and printed a raw traceback at the operator."""
        row = _row(runtime_flags=flags)

        with _status_harness(row=row):
            result = runner.invoke(live, ["status", "alpha-session"])

        assert result.exit_code == 0
        assert result.exception is None
        assert "Traceback" not in result.output
        assert "state: running" in result.output


class TestTraderIdIsAStatusOnlyField:
    """`list` deliberately carries no trader id — pinned so the asymmetry stays
    a policy rather than reverting to the omitted keyword argument it was."""

    def test_the_list_table_shows_no_trader_id(self, runner):
        row = _row(id=3, name="no-trader-id-session")
        repo = MagicMock()
        repo.find_all.return_value = [row]
        repo.trade_counts_by_session.return_value = {3: (0, 0)}

        with (
            patch(_STATUS_GET_SYNC_SESSION, _fake_session_cm),
            patch(_STATUS_SESSION_REPO, MagicMock(return_value=repo)),
        ):
            result = runner.invoke(live, ["list"])

        assert result.exit_code == 0
        assert derive_trader_id(row.session_id) not in result.output

    def test_status_still_shows_it(self, runner):
        with _status_harness() as spies:
            result = runner.invoke(live, ["status", "alpha-session"])

        assert derive_trader_id(spies["row"].session_id) in result.output
