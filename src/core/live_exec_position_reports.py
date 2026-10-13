"""Switch off the IB adapter's own "external position change" reports (Story 4.3).

**This module removes an upstream behaviour on purpose** (decision D-B, PO ruling
1A, 2026-09-27), in the ``live_exec_avg_px`` shape: a narrow patch of the IB
execution client, applied where the node builder's factory builds it.

What it removes, read off the installed 1.220.0 wheel and driven for real by
``tests/component/core/test_live_exec_position_reports.py``:
``_connect`` subscribes ``positionUpdate-{account}`` to ``_on_position_update``,
which schedules ``self._handle_position_update(p)``. That handler compares the
broker's quantity with its own ``_known_positions`` and, on any difference,
sends a ``PositionStatusReport`` straight to the engine, which corrects the
cache through a synthetic ``INTERNAL-DIFF`` fill. Two measured defects make that
path wrong for this system:

1. **The phantom** (P11 and P12, live). A flat instrument is untracked, so the
   broker's ``0 → 22`` update arriving before the strategy's own ``execDetails``
   is reported as external; the ``execDetails`` then makes the tracked quantity
   44, and the next ``22`` update is reported again. One spurious round trip on
   every strategy entry, and two would-be discrepancies that are one race.
2. **Blind to flat.** An update to quantity ``0`` returns without a report, so
   a position closed outside the session is never reported at all.

Runtime position alignment is Story 4.3's verified cycle instead
(:mod:`src.core.live_runtime_reconcile`): it compares the cache with the broker,
waits for a disagreement to repeat, and corrects it through the framework's own
``reconcile_execution_report`` — logging every correction as
``reconcile.discrepancy``. The accepted cost is latency: a change the execution
stream did not explain is corrected within one to two cycles, not on arrival.

The patch is an **instance** attribute: ``_on_position_update`` looks the
handler up on ``self`` at call time, so shadowing it on the client is enough and
the class is untouched. It never sends, never raises, and logs one
:data:`DEFERRED_EVENT` per update — never the account the update carries
(NFR26).

**How this module should die.** ``TestTheAdapterDefect`` pins both defects
against the adapter's real code; when an upgrade fixes them, those tests fail by
name, and whether to hand runtime positions back to the adapter becomes a
decision rather than a discovery.
"""

from typing import Any

import structlog

__all__ = [
    "DEFERRED_EVENT",
    "HANDLER_ATTRIBUTE",
    "TRACKING_ATTRIBUTE",
    "install_position_report_suppression",
]

#: AR41's ``reconcile.*`` namespace: a broker position update arrived and was
#: deliberately not turned into a report.
DEFERRED_EVENT = "reconcile.position_update_deferred"

#: The adapter-private names this module depends on, each pinned against the
#: wheel's source. A rename upstream would otherwise make the patch a silent
#: no-op and bring the phantom back.
HANDLER_ATTRIBUTE = "_handle_position_update"
TRACKING_ATTRIBUTE = "_known_positions"

_DETAIL = (
    "not reported to the engine; Story 4.3's runtime reconciliation cycle verifies positions "
    "against the broker and corrects any disagreement through the framework"
)

logger = structlog.get_logger(__name__)


def install_position_report_suppression(client: Any) -> None:
    """Replace ``client._handle_position_update`` with one that never reports.

    Args:
        client: The IB execution client, immediately after the factory built it
            and before the node starts.

    Raises:
        AttributeError: If the client lacks ``_handle_position_update`` or
            ``_known_positions`` — the upstream shape this targets has moved.
            Loud at build time on purpose, the ``live_exec_avg_px`` precedent.
    """
    for name in (HANDLER_ATTRIBUTE, TRACKING_ATTRIBUTE):
        getattr(client, name)

    async def _handle_position_update(ib_position: Any) -> None:
        _note_deferred(client, ib_position)

    setattr(client, HANDLER_ATTRIBUTE, _handle_position_update)


def _note_deferred(client: Any, ib_position: Any) -> None:
    """Log the update without ever raising — it runs inside an adapter task."""
    try:
        con_id = ib_position.contract.conId
        known = getattr(client, TRACKING_ATTRIBUTE).get(con_id)
        logger.info(
            DEFERRED_EVENT,
            con_id=con_id,
            known_quantity=None if known is None else str(known),
            reported_quantity=str(ib_position.quantity),
            detail=_DETAIL,
        )
    except Exception:  # noqa: BLE001 - a diagnostic must never be what fails
        pass
