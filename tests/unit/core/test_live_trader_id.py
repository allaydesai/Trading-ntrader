"""Unit tests for the session-derived Nautilus trader_id (Story 2.4, AC #2).

Unit tier, deliberately: ``src/core/live_trader_id.py`` imports neither
``nautilus_trader`` nor ``sqlalchemy``, and ``TestImportPurity`` below is what
keeps that true. The one assertion that genuinely needs Nautilus — that
``TraderId.get_tag()`` round-trips the derived tag — lives in the component
tier instead (``tests/component/core/test_live_cache.py``), so this module can
stay framework-free.
"""

import subprocess
import sys
from uuid import UUID, uuid4

import pytest

from src.core.live_trader_id import (
    SHORT_SESSION_ID_LENGTH,
    TRADER_ID_PREFIX,
    derive_trader_id,
)


@pytest.mark.unit
class TestDeterminism:
    """AC #2: the identical value on every process run of that session."""

    def test_the_same_session_always_derives_the_same_trader_id(self):
        """Repeated calls agree — this is the whole of "deterministic"."""
        session_id = UUID("0e8f1c2a-3b4d-4e6f-8081-920304050607")

        derived = {derive_trader_id(session_id) for _ in range(50)}

        assert derived == {"PAPER-0e8f1c2a"}

    def test_derivation_survives_a_fresh_interpreter(self):
        """No process-local salt, seed or clock may leak into the value.

        A same-process loop cannot catch a derivation that mixes in
        ``id()``, ``hash()`` of a str under PYTHONHASHSEED, or a random
        salt — all of which are stable within one interpreter and differ
        across the restart this AC is actually about.
        """
        session_id = "0e8f1c2a-3b4d-4e6f-8081-920304050607"
        program = (
            "from uuid import UUID;"
            "from src.core.live_trader_id import derive_trader_id;"
            f"print(derive_trader_id(UUID('{session_id}')))"
        )

        first = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=True
        )
        second = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=True
        )

        assert first.stdout.strip() == second.stdout.strip() == "PAPER-0e8f1c2a"


@pytest.mark.unit
class TestDistinctness:
    """AC #2: two different sessions produce different trader_id values."""

    def test_two_sessions_derive_different_trader_ids(self):
        """The ordinary case, stated as the AC states it."""
        first = derive_trader_id(UUID("0e8f1c2a-3b4d-4e6f-8081-920304050607"))
        second = derive_trader_id(UUID("ffffffff-3b4d-4e6f-8081-920304050607"))

        assert first != second

    def test_a_thousand_random_sessions_derive_a_thousand_distinct_ids(self):
        """A smoke bound on the truncation, not a proof of injectivity.

        8 hex characters is 2**32 values, so this is expected to pass
        essentially always at n=1000 (birthday bound ~1 in 10**4) and is
        here to catch a derivation that ignores most of its input — a
        constant, a version byte, or a slice taken from the wrong end.
        """
        derived = {derive_trader_id(uuid4()) for _ in range(1000)}

        assert len(derived) == 1000


@pytest.mark.unit
class TestFormat:
    """AC #2: the value is ``PAPER-<short-session-id>``."""

    def test_the_value_is_the_prefix_and_eight_hex_characters(self):
        """Exact shape, asserted against the constants rather than a literal."""
        session_id = UUID("0e8f1c2a-3b4d-4e6f-8081-920304050607")

        derived = derive_trader_id(session_id)

        prefix, _, tag = derived.partition("-")
        assert prefix == TRADER_ID_PREFIX
        assert tag == session_id.hex[:SHORT_SESSION_ID_LENGTH]
        assert len(tag) == SHORT_SESSION_ID_LENGTH

    def test_the_tag_contains_no_hyphen(self):
        """The load-bearing one — ``TraderId.get_tag()`` splits on the LAST hyphen.

        ``str(uuid)`` carries four hyphens, so a ``str()``-instead-of-``.hex``
        slip produces a *valid* ``TraderId`` whose tag is the UUID's last
        12 characters. Client order IDs embed that tag
        (``common/generators.pyx:145-151``), so the mistake would silently
        change every order ID a restarted session emits. Nothing raises.
        """
        for _ in range(200):
            _, _, tag = derive_trader_id(uuid4()).partition("-")
            assert "-" not in tag

    def test_the_tag_is_lowercase_hex(self):
        """Canonical form, so two derivations of one session compare equal."""
        for _ in range(100):
            _, _, tag = derive_trader_id(uuid4()).partition("-")
            assert tag == tag.lower()
            assert all(character in "0123456789abcdef" for character in tag)

    def test_the_prefix_is_paper(self):
        """PAPER is a safety signal, visible in TWS on every client order ID."""
        assert TRADER_ID_PREFIX == "PAPER"
        assert derive_trader_id(uuid4()).startswith("PAPER-")


@pytest.mark.unit
class TestRejectsWrongInput:
    """A session *name* must never be mistaken for a session id."""

    @pytest.mark.parametrize(
        "wrong",
        [
            "0e8f1c2a-3b4d-4e6f-8081-920304050607",  # the UUID, but as a str
            "my-session",
            None,
            42,
            b"0e8f1c2a",
        ],
    )
    def test_a_non_uuid_argument_raises_type_error(self, wrong):
        """Accepting a str would bind a session to another session's namespace.

        ``live create`` takes a ``--name``, and both a name and a
        ``session_id`` are strings at the CLI boundary. A permissive
        signature turns that confusion into a plausible-looking trader_id
        for the wrong session, which is the exact cross-contamination this
        story exists to prevent.
        """
        with pytest.raises(TypeError):
            derive_trader_id(wrong)

    def test_the_type_error_names_what_was_passed(self):
        """The message must be actionable at a CLI boundary."""
        with pytest.raises(TypeError, match="str"):
            derive_trader_id("0e8f1c2a-3b4d-4e6f-8081-920304050607")


@pytest.mark.unit
class TestImportPurity:
    """AR38: this module must stay reachable from Nautilus-free layers.

    ``SessionService`` may never import Nautilus, and Story 2.8's ``live
    status`` will want to show a session's trader_id. Template copied from
    ``tests/unit/models/test_session_spec.py``.
    """

    @pytest.mark.parametrize("forbidden", ["nautilus_trader", "sqlalchemy"])
    def test_importing_the_module_leaks_no_framework(self, forbidden):
        """A fresh interpreter importing this module pulls in neither."""
        program = (
            "import sys;"
            "import src.core.live_trader_id;"
            f"leaked=[m for m in sys.modules if m.split('.')[0]=='{forbidden}'];"
            "print(len(leaked))"
        )

        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=True
        )

        assert result.stdout.strip() == "0", (
            f"importing src.core.live_trader_id leaked {forbidden} modules; "
            "it must stay importable from layers AR38 keeps framework-free"
        )
