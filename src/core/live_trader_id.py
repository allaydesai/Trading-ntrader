"""The session-derived Nautilus ``trader_id`` (Story 2.4, AR10).

Owns: ``derive_trader_id()`` — the single sanctioned way to turn a session's
UUID business key into the ``trader_id`` its Nautilus node runs under, and with
it the Redis key namespace that node reads and writes.

Does not own: the ``CacheConfig`` that points at Redis
(``src/core/live_cache.py``), node assembly (``src/core/live_node_builder.py``),
the session record (``src/services/session_service.py``), or persisting the
result — the value is **derived on demand and never stored**, so no row can
ever disagree with its own ``session_id``.

Deliberately framework-free: standard library only. No ``nautilus_trader``, no
``sqlalchemy``, no settings read. ``SessionService`` may never import Nautilus
(AR38) and Story 2.8's ``live status`` will want to show a session's
``trader_id``, so this has to be reachable from layers that keep those imports
out. ``tests/unit/core/test_live_trader_id.py::TestImportPurity`` keeps it true.

Why the exact shape matters, beyond looking tidy:

1. **The tag must contain no hyphen.** Nautilus's ``TraderId.get_tag()`` returns
   the substring after the **last** ``-``, and ``ClientOrderIdGenerator`` embeds
   that tag in every client order ID it emits
   (``common/generators.pyx:40, 145-151``). Deriving from ``str(uuid)`` — which
   carries four hyphens — yields a perfectly valid ``TraderId`` whose tag is the
   UUID's last 12 characters, silently changing every order ID. Hence ``.hex``.
2. **Determinism is what makes a restart a restart.** ``Strategy.on_start``
   restores its client-order-ID counter from the cache
   (``trading/strategy.pyx:353-368``). That only rejoins the right counter if
   this function returns the same value it returned last process run — which is
   what stops a resumed session reissuing client order IDs it has already used
   (NFR6, AR23).

Known, accepted limit: eight hex characters is 2**32 values, so distinctness
across sessions is overwhelmingly likely rather than guaranteed by construction
(~1 in 10**7 at 100 sessions, ~1 in 10**4 at 1000). ``TraderId`` would accept the
full 32-character hex, but AR10 specifies ``PAPER-<short-session-id>`` and the
tag is read by operators in TWS. Recorded in ``deferred-work.md`` with the
arithmetic rather than left to be rediscovered.
"""

from uuid import UUID

#: Hardcoded, not configurable. It doubles as a safety signal: every client
#: order ID this system emits carries ``PAPER``, visible in TWS and in IBKR's
#: own order log. A configurable prefix would let one misconfiguration erase
#: that signal. Real-money crossing (AR15) is designed but unexercised; the
#: story that exercises it owns the prefix question.
TRADER_ID_PREFIX = "PAPER"

#: AR10's "short". See the module docstring's note on the collision bound.
SHORT_SESSION_ID_LENGTH = 8


def derive_trader_id(session_id: UUID) -> str:
    """Derive the Nautilus ``trader_id`` for a session, deterministically (AR10).

    Args:
        session_id: The session's UUID business key —
            ``TradingSession.session_id``, not its ``name`` and not its
            ``id``. Typed strictly: a ``str`` is refused rather than coerced,
            because a session's ``name`` and its ``session_id`` are both
            strings at the CLI boundary, and accepting either would bind a
            session to another session's Redis namespace with no error.

    Returns:
        ``PAPER-<first 8 lowercase hex characters of the UUID>``, e.g.
        ``"PAPER-0e8f1c2a"``. The same input always returns the same output,
        in this process and in every future one.

    Raises:
        TypeError: ``session_id`` is not a ``UUID``.
    """
    if not isinstance(session_id, UUID):
        raise TypeError(
            f"derive_trader_id() takes a UUID session_id, got {type(session_id).__name__}. "
            "Pass TradingSession.session_id — a session's name is not its identifier here, "
            "and deriving from the wrong one would point this session at another one's "
            "engine cache."
        )
    # `.hex`, never `str()`: see the module docstring. `UUID.hex` is already
    # lowercase and hyphen-free, so no further normalisation is needed — but the
    # tests assert both properties rather than trusting that to stay true.
    return f"{TRADER_ID_PREFIX}-{session_id.hex[:SHORT_SESSION_ID_LENGTH]}"
