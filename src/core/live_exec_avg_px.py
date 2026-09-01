"""Keep an IBKR fill encodable, so the fill event actually gets published.

**This module is a workaround for an upstream defect, not a feature.** It exists
because of one measured fact, found live on 2026-09-01 in the first session that
ever received a real IBKR fill: the fill killed the ``ExecEngine`` and the node
shut itself down, while the order had really filled at the broker.

The chain, read off the installed 1.220.0 wheel:

1. The IB execution client stores an order's average fill price as a ``Price``
   **object** — ``dict[ClientOrderId, Price]``
   (``adapters/interactive_brokers/execution.py:188``), written at ``:1037``
   from ``instrument.make_price(...)``.
2. It copies that object straight into the fill event's free-form ``info`` dict
   (``:1096``, plus the two spread paths at ``:1229`` and ``:1312``).
3. ``OrderFilled.to_dict_c`` stringifies every other field but passes ``info``
   through by reference (``model/events/order.pyx:4810``).
4. A live session always runs with the Redis-backed cache database (AR10,
   :mod:`src.core.live_cache`), so every fill is serialized:
   ``ExecutionEngine._apply_event_to_order`` → ``Cache.update_order`` →
   ``CacheDatabaseAdapter.update_order`` (``cache/database.pyx:1159``) →
   ``MsgSpecSerializer.serialize``, which raises
   ``TypeError: Encoding objects of type ...Price is unsupported``.

A backtest has no cache database, which is why nothing before this ever saw it.

**Why it is worked around rather than tolerated.** The raise happens in
``_apply_event_to_order``, which runs *before*
``publish_c(topic=f"events.order.{...}")`` at ``execution/engine.pyx:1176``. So
the ``OrderFilled`` is never published at all: ``OrderEventObserver`` writes no
``order.filled`` record, ``Portfolio`` never updates, no position event fires.
The order fills at the broker and the system's entire record of it is a
traceback. Story 3.3's NFR21 evidence is unobtainable while this stands.

**The fix, and why it is safe.** ``_order_avg_prices`` is written at exactly one
site and read at exactly three, and all three reads do nothing but assign into
``info["avg_px"]`` — no arithmetic, no comparison. Storing ``str(price)``
instead of the ``Price`` therefore changes only the rendering of one free-form
metadata value, to the same text ``to_dict_c`` already uses for ``last_px``
(``order.pyx:4803``). ``tests/component/core/test_live_exec_avg_px.py`` pins
both halves of that claim against the adapter's own source, so a future version
that does arithmetic on a stored value goes red rather than breaking silently.

**How this module should die.** ``TestTheWheelDefect`` pins the upstream
behaviour as canaries. When a nautilus_trader upgrade fixes the adapter or
teaches the serializer about ``Price``, those tests fail by name, and this
module plus its single call site in :mod:`src.core.live_node_builder` can be
deleted. Reported upstream rather than carried silently.
"""

from typing import Any

__all__ = [
    "AVG_PX_ATTRIBUTE",
    "StringifiedAvgPrices",
    "install_avg_px_serialization_fix",
]


AVG_PX_ATTRIBUTE = "_order_avg_prices"
"""The adapter-private attribute this module replaces, named once.

Pinned against the adapter's source by
``test_the_adapter_still_keeps_average_prices_under_the_patched_name`` — a
rename upstream would otherwise turn the install into a silent no-op and the
crash would return live.
"""


class StringifiedAvgPrices(dict):
    """A ``dict`` that stores ``str(value)``, so its values stay encodable.

    Deliberately a ``dict`` subclass rather than a wrapper: the adapter reads it
    with ordinary ``in`` and ``[]`` (``execution.py:1095-1096``), and a subclass
    keeps every one of those operations working without this module having to
    anticipate them.
    """

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, str(value))


def install_avg_px_serialization_fix(client: Any) -> None:
    """Replace ``client._order_avg_prices`` with a stringifying map.

    Args:
        client: The IB execution client to patch, immediately after the factory
            built it and before the node starts.

    Raises:
        AttributeError: If the client has no ``_order_avg_prices`` attribute —
            meaning the upstream shape this workaround targets has moved. Loud
            at build time on purpose: a silent skip here reproduces the exact
            2026-09-01 outcome, an order that reaches the broker and a node that
            dies acknowledging it.
    """
    existing = getattr(client, AVG_PX_ATTRIBUTE)
    patched = StringifiedAvgPrices()
    for key, value in existing.items():
        patched[key] = value
    setattr(client, AVG_PX_ATTRIBUTE, patched)
