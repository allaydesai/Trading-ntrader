"""The Epic-1 acceptance criteria, as an addressable manifest.

Transcribed from ``_bmad-output/planning-artifacts/prd-epic1-scope.md`` — one
entry per Given/When/Then block, in the order the document states them. This is
the single source of truth for the ids the acceptance suite marks itself with:
:func:`criterion` refuses an id that is not in here, and
``test_epic1_ac_safety.py::TestTheAcceptanceSuiteItself`` refuses an id in here
that no test claims. Neither the list nor the suite can drift from the other
without a red test.

The labels are deliberately short. They are printed one-per-line by the
acceptance-criteria terminal summary in ``tests/conftest.py``, which is what a
reviewer reads to check coverage; the full criterion text lives in the PRD
extract this file names, and each test's docstring quotes the part it pins.
"""

import pytest

#: The document these criteria are transcribed from, printed in the summary so
#: the table is traceable back to its source without opening the code.
SOURCE = "_bmad-output/planning-artifacts/prd-epic1-scope.md"

#: (story id, ((criterion id, label), ...)) in PRD order.
STORIES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "1.1",
        (
            ("1.1a", "evaluate_gate decides with no socket/file/db I/O"),
            ("1.1b", "live_gate imports no framework or I/O library"),
            ("1.1c", "paper mode + paper port + DU/DF account permits"),
            ("1.1d", "each failing condition refuses, naming itself"),
            ("1.1e", "an unrecognised port refuses, i.e. fails closed"),
            ("1.1f", "real money needs both declarations, matching exactly"),
            ("1.1g", "refusal messages mask accounts to their last 3 chars"),
            ("1.1h", "the unit truth-table suite exists and needs no broker"),
        ),
    ),
    (
        "1.2",
        (
            ("1.2a", "ibkr_live_client_id defaults to 10, env-configurable"),
            ("1.2b", "equal client ids are rejected by a model validator"),
            ("1.2c", "field docs state historical/live/reconcile allocation"),
        ),
    ),
    (
        "1.3",
        (
            ("1.3a", "the config carries the IB data and exec client configs"),
            ("1.3b", "both IB live factories are registered on the node"),
            ("1.3c", "the gate runs first; a refusal raises before any client"),
            ("1.3d", "ibkr_read_only=False, process-scoped, never a control"),
            ("1.3e", "the log guard registers, and coexists with a backtest"),
            ("1.3f", "both clients use the live id, not the historical one"),
            ("1.3g", "shutdown leaks nothing; a second node still builds"),
            ("1.3h", "no new dependency; the IB adapter ships with nautilus"),
        ),
    ),
    (
        "1.4",
        (
            ("1.4a", "a DU/DF reported account is required to proceed"),
            ("1.4b", "a non-paper account stops the node, starts no strategy"),
            ("1.4c", "verification logs mask the account to its last 3 chars"),
            ("1.4d", "it runs after connect, strictly before any strategy"),
        ),
    ),
    (
        "1.5",
        (
            ("1.5a", "market data type is REALTIME, not DELAYED_FROZEN"),
            ("1.5b", "a delayed feed fails loudly, never falls back quietly"),
            ("1.5c", "bars are restricted to RTH via ibkr_use_rth=True"),
            ("1.5d", "a closed bar is delivered, logged with id and timestamp"),
            ("1.5e", "an over-budget session names the limit and the count"),
            ("1.5f", "the 45 req/s pacing governs subscription dispatch"),
        ),
    ),
    (
        "1.6",
        (
            ("1.6a", "a drop logs connection.lost and withdraws permission"),
            ("1.6b", "permission returns only after state is re-established"),
            ("1.6c", "reconnect lands within 60s, elapsed time in the logs"),
            ("1.6d", "the daily gateway restart is an expected reconnect"),
            ("1.6e", "a sustained outage halts; it never runs on stale state"),
        ),
    ),
    (
        "1.7",
        (
            ("1.7a", "live check runs the whole sequence and exits 0"),
            ("1.7b", "a gate refusal prints the reason and exits 3"),
            ("1.7c", "nothing is constructed or connected on the refusal path"),
            ("1.7d", "a gate pass with an unreachable broker exits 4"),
            ("1.7e", "output is structlog console, wired like the other groups"),
            ("1.7f", "live --help lists check; the group is in cli/main.py"),
        ),
    ),
)

#: Criterion id -> label, flattened.
CRITERIA: dict[str, str] = {
    ac_id: label for _story, criteria in STORIES for ac_id, label in criteria
}


def criterion(ac_id: str):
    """Mark a test as the evidence for one acceptance criterion.

    Takes the id only — the label comes from :data:`CRITERIA`, so the summary
    table and this manifest cannot disagree. An unknown id raises at import
    time rather than producing a mystery row in the report.
    """
    try:
        label = CRITERIA[ac_id]
    except KeyError:
        raise KeyError(
            f"{ac_id!r} is not an Epic-1 acceptance criterion. Known ids: "
            f"{', '.join(CRITERIA)}. Add it to STORIES in {__name__} first, "
            "quoting the PRD block it comes from."
        ) from None
    return pytest.mark.acceptance_criterion(ac_id, label, SOURCE)
