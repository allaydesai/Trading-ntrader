"""AC #7, #8 guards (Story 2.2): repository shape, no database needed.

These live in the **unit** tier deliberately. ``.github/workflows/ci.yml`` passes
``--ignore=tests/integration/db`` on both the integration job (line 168) and the
coverage job (line 238), so the entire ``tests/integration/db/`` directory never
runs in CI — a guard placed there gates nothing on a PR. The story's own Testing
Standards say as much ("Anything that must gate a PR belongs in the unit tier"),
which is why the migration guard was placed here too; these three came back from
code review for the same reason.

The behavioural proof that these repositories work against real Postgres stays in
``tests/integration/db/test_trading_session_repository.py``. What is guarded here
is the *shape*: that no write-back path to the frozen ``spec`` column exists, and
that the async and sync twins keep matching capability sets.
"""

import pytest

from src.db.repositories.trading_session_repository import TradingSessionRepository
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository

#: The complete public surface AC #8 fixes for both repositories. An allowlist,
#: not a prefix test: `startswith("update")` alone would let `set_spec`, `save`,
#: `patch_spec` or `merge` through, and AC #7 is satisfied by there being no
#: write path *of any name*.
EXPECTED_CAPABILITIES = frozenset({"create", "find_by_session_id", "find_by_name", "find_all"})


def _public_names(repository_class) -> frozenset:
    """Public attribute names, minus the injected `session` handle."""
    return frozenset(
        name
        for name in dir(repository_class)
        if not name.startswith("_") and name not in ("session",)
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "repository_class",
    [TradingSessionRepository, SyncTradingSessionRepository],
    ids=["async", "sync"],
)
def test_repository_exposes_exactly_the_expected_capabilities(repository_class):
    """AC #7 and #8: the spec is write-once by absence of any setter, of any name."""
    assert _public_names(repository_class) == EXPECTED_CAPABILITIES


@pytest.mark.unit
def test_both_repositories_expose_the_same_capability_set():
    """AC #8: matching capabilities, even though only sync is exercised this phase."""
    assert _public_names(TradingSessionRepository) == _public_names(SyncTradingSessionRepository)


@pytest.mark.unit
@pytest.mark.parametrize(
    "repository_class",
    [TradingSessionRepository, SyncTradingSessionRepository],
    ids=["async", "sync"],
)
def test_no_method_can_write_the_spec_column(repository_class):
    """AC #7 stated as the property, not as a name check.

    ``spec`` is written once by ``create`` and never again. Any method whose
    name suggests mutation is a regression, whatever it is called.
    """
    mutators = {
        name
        for name in _public_names(repository_class)
        if name.startswith(("update", "set_", "save", "patch", "merge", "upsert", "replace"))
    }
    assert not mutators, f"unexpected write path(s): {sorted(mutators)}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "repository_class",
    [TradingSessionRepository, SyncTradingSessionRepository],
    ids=["async", "sync"],
)
def test_find_by_session_id_can_lock_the_row_for_update(repository_class):
    """Story 2.3 AC #6: the reclaim decision reads the row locked and fresh.

    A structural check, not a behavioural one — locking only matters under
    real concurrent transactions, which is what
    ``tests/integration/db/test_session_service.py``'s race test proves. This
    guard only proves the *capability* exists on both twins (AR9), so
    ``SessionService.transition()`` can ask for it.

    Asserted over the AST rather than as a substring, because a substring check
    passes when the text merely appears in a comment or docstring, when the
    guard is inverted, or when the return value is dropped
    (``stmt.with_for_update()`` without reassignment — an easy real bug, since
    SQLAlchemy statements are immutable). The ``populate_existing`` half is
    included because the lock is inert without it: the ORM would return the
    identity-mapped copy and discard the freshly locked row's values, which was
    demonstrated as two processes both reclaiming one session.
    """
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(repository_class.find_by_session_id))
    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "for_update" in inspect.signature(repository_class.find_by_session_id).parameters
    assert "with_for_update" in calls, "the locked read must call with_for_update()"
    assert "execution_options" in calls, "the locked read must set populate_existing"
    assert "populate_existing=True" in source.replace(" ", "")


@pytest.mark.unit
@pytest.mark.parametrize(
    "repository_class",
    [TradingSessionRepository, SyncTradingSessionRepository],
    ids=["async", "sync"],
)
def test_find_all_orders_deterministically(repository_class):
    """Story 2.8's `live list` renders whatever `find_all` returns.

    Without an ORDER BY, Postgres may return rows in any order, and that order
    changes after an UPDATE or a VACUUM — so the same command run twice can
    reorder for no reason. Compiling the statement needs no database.
    """
    import inspect

    source = inspect.getsource(repository_class.find_all)

    assert "order_by(" in source, "find_all must impose a deterministic order for Story 2.8's list"
