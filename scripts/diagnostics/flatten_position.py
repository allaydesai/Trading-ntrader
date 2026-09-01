"""Close one position on the paper account, deliberately and with confirmation.

Written 2026-09-01 to clear the ``SHORT 23 NVDA.NASDAQ`` position that the
crashed P7 run left behind: the order filled at the broker, but the fill killed
the ``ExecEngine`` before anything downstream heard about it (the defect
:mod:`src.core.live_exec_avg_px` works around), so the position existed with no
session owning it.

**This is the only tool in the repository that submits an order on purpose.**
Everything else that can trade does so as a side effect of a strategy's own
logic. It is therefore built to be hard to misfire:

- The Layer 1 and Layer 2 safety gates both run, unchanged and unbypassable, so
  this can only ever reach a paper account. There is no ``--real-money`` flag and
  no way to reach one from here.
- It **reads the position from the broker** after reconciliation and refuses
  unless the requested side and quantity exactly offset what is actually held.
  The operator's ``--side``/``--quantity`` are a confirmation to be checked, not
  an instruction to be trusted.
- Without ``--confirm`` it connects, reports, and submits nothing.

Usage:
    PYTHONPATH=. uv run python scripts/diagnostics/flatten_position.py \
        --instrument NVDA.NASDAQ --side BUY --quantity 23 --confirm
"""

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

from nautilus_trader.model.enums import OrderSide  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId  # noqa: E402
from nautilus_trader.model.objects import Quantity  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

from src.config import IBKRSettings  # noqa: E402
from src.core.live_gate import GateFlags  # noqa: E402
from src.core.live_node_builder import (  # noqa: E402
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node,
)

FLATTEN_TRADER_ID = "PAPER-FLATTEN01"

_CONNECT_POLL_SECONDS = 0.25
_FILL_POLL_SECONDS = 0.25


class FlattenError(RuntimeError):
    """The tool reached the gateway but could not complete the close."""


class _FlattenStrategy(Strategy):
    """Submit exactly one market order on start, then record what comes back.

    Deliberately defined here rather than under ``src/core/strategies/``: that
    package is scanned by the Story 3.1 stop-path guards and carries a
    zero-diff evidence contract for Stories 3.2 and 3.3. A diagnostic that
    submits an order has no business inside it.
    """

    def __init__(self, instrument_id: InstrumentId, side: OrderSide, quantity: int) -> None:
        super().__init__()
        self._instrument_id = instrument_id
        self._side = side
        self._quantity = quantity
        self.submitted: list = []
        self.fills: list = []
        self.rejections: list = []

    def on_start(self) -> None:
        instrument = self.cache.instrument(self._instrument_id)
        if instrument is None:
            self.log.error(f"instrument not in cache: {self._instrument_id}")
            return
        order = self.order_factory.market(
            instrument_id=self._instrument_id,
            order_side=self._side,
            quantity=Quantity.from_int(self._quantity),
        )
        self.submitted.append(order)
        self.submit_order(order)

    def on_order_filled(self, event) -> None:
        self.fills.append(event)

    def on_order_rejected(self, event) -> None:
        self.rejections.append(event)

    def on_order_denied(self, event) -> None:
        self.rejections.append(event)


async def _await_connected(node, run_task: asyncio.Task, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run_task.done():
            run_task.result()
            raise FlattenError("node stopped running before it reported connected")
        if node.kernel.data_engine.check_connected() and node.kernel.exec_engine.check_connected():
            return
        await asyncio.sleep(_CONNECT_POLL_SECONDS)
    raise FlattenError(f"engines did not connect within {timeout}s")


def _held_net_quantity(node, instrument_id: InstrumentId) -> int:
    """Net signed quantity the broker reports for this instrument.

    Positive is long, negative is short. Read from the cache *after*
    reconciliation, so it is the broker's view rather than this process's.
    """
    net = 0
    for position in node.kernel.cache.positions_open():
        if position.instrument_id != instrument_id:
            continue
        net += int(position.signed_qty)
    return net


def _check_offsets(held: int, side: OrderSide, quantity: int) -> None:
    """Refuse anything that is not exactly a close of what is held."""
    if held == 0:
        raise FlattenError("the broker reports no open position to close (net=0)")
    wanted_side = OrderSide.BUY if held < 0 else OrderSide.SELL
    if side != wanted_side:
        raise FlattenError(
            f"refusing: net position is {held:+d}, which is closed by "
            f"{wanted_side.name}, but --side {side.name} was requested"
        )
    if quantity != abs(held):
        raise FlattenError(
            f"refusing: net position is {held:+d}, so the close is "
            f"{abs(held)} units, but --quantity {quantity} was requested"
        )


def _shutdown(node, run_task, loop) -> None:
    try:
        node.stop()
        loop.run_until_complete(asyncio.wait_for(asyncio.shield(run_task), timeout=30))
    except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError, Exception):
        pass
    finally:
        node.dispose()


def _run(
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: int,
    *,
    confirm: bool,
    build=build_trading_node,
) -> str:
    """Connect, read the broker's position, and — only if armed — close it.

    Args:
        build: Seam for the component tests, which drive this whole function
            against a fake node. The arming order below is the tool's single
            safety property and was once wrong in a way no test could see; it
            is now asserted by driving this function rather than by reading it.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = IBKRSettings()
    bar_type = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
    print(
        f"[flatten] building node host={settings.ibkr_host} port={settings.ibkr_port} "
        f"client_id={settings.ibkr_live_client_id} trader_id={FLATTEN_TRADER_ID}",
        flush=True,
    )
    # The bar type is passed only so the instrument reaches both providers'
    # `load_ids` — without it the exec client cannot translate an order for it
    # (`adapters/interactive_brokers/execution.py:525`, the Story 3.2 defect).
    node = build(
        settings,
        trader_id=FLATTEN_TRADER_ID,
        bar_types=[bar_type],
        cli_flags=GateFlags(),
        loop=loop,
    )

    # The strategy is deliberately NOT constructed or added here, and this is
    # the single most important line in the file.
    #
    # Measured 2026-09-01, by running this tool WITHOUT `--confirm` and watching
    # it fill a 22-share SELL: `TradingNodeKernel.start_async` ends with
    # `self._trader.start()` (`system/kernel.py:1027`), which starts every
    # strategy already added — so a strategy added before `run_async()` submits
    # from `on_start` before any check in this function has run, and `--confirm`
    # gates nothing at all.
    #
    # `live_session_runner.py` is safe from this by construction, not by luck:
    # it does not add a strategy until `_phase_trading`, long after
    # `_phase_node_connect` started the node (`:507` then `:659-660`), so
    # `_trader.start()` finds nothing to start. An earlier version of this
    # comment cited `:660` as proof that `run_async()` does not start
    # strategies. That was a misreading of why the runner is safe.
    #
    # This tool now follows the same discipline: nothing that can submit an
    # order exists in the trader until the broker has been read and the
    # operator has armed it.
    print("[flatten] node built; building clients...", flush=True)
    node.build()

    print("[flatten] starting node...", flush=True)
    run_task = loop.create_task(node.run_async())

    try:
        print(
            f"[flatten] waiting up to {settings.ibkr_connection_timeout}s for engines...",
            flush=True,
        )
        loop.run_until_complete(
            _await_connected(node, run_task, float(settings.ibkr_connection_timeout))
        )
        print("[flatten] connected (data + exec)", flush=True)

        # Let startup reconciliation report the broker's real positions.
        loop.run_until_complete(asyncio.sleep(5))

        held = _held_net_quantity(node, instrument_id)
        print(f"[flatten] broker reports net {held:+d} {instrument_id}", flush=True)
        _check_offsets(held, side, quantity)
        print(
            f"[flatten] {side.name} {quantity} {instrument_id} would exactly close it",
            flush=True,
        )

        if not confirm:
            return (
                f"held={held:+d} submitted=0 (dry run — nothing that can submit "
                "an order was ever added to the trader)"
            )

        print(f"[flatten] submitting {side.name} {quantity} {instrument_id}...", flush=True)
        # Arming, in the only order that is safe: construct the strategy, add it
        # to an already-running trader (`add_strategy` never auto-starts — it
        # rejects a RUNNING strategy outright), then start it. Every check this
        # tool makes is upstream of this line.
        strategy = _FlattenStrategy(instrument_id, side, quantity)
        node.trader.add_strategy(strategy)
        node.trader.start_strategy(strategy.id)
        loop.run_until_complete(_await_fill(strategy, timeout=60.0))

        for fill in strategy.fills:
            print(
                f"[flatten] FILL qty={fill.last_qty} px={fill.last_px} "
                f"commission={fill.commission} trade_id={fill.trade_id} "
                f"venue_order_id={fill.venue_order_id}",
                flush=True,
            )
        for rejection in strategy.rejections:
            print(f"[flatten] REJECTED/DENIED: {rejection}", flush=True)

        remaining = _held_net_quantity(node, instrument_id)
        print(f"[flatten] net after: {remaining:+d} {instrument_id}", flush=True)
        return f"held={held:+d} fills={len(strategy.fills)} remaining={remaining:+d}"
    finally:
        print("[flatten] stopping and disposing node...", flush=True)
        _shutdown(node, run_task, loop)
        print(
            f"[flatten] disposed loop.is_closed={loop.is_closed()}",
            flush=True,
        )


async def _await_fill(strategy: _FlattenStrategy, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if strategy.fills or strategy.rejections:
            # Let a partial-fill series finish arriving before reporting.
            await asyncio.sleep(2)
            return
        await asyncio.sleep(_FILL_POLL_SECONDS)
    raise FlattenError(f"no fill or rejection arrived within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", required=True, help="e.g. NVDA.NASDAQ")
    parser.add_argument("--side", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--quantity", required=True, type=int)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually submit the order; without it this is a read-only dry run",
    )
    args = parser.parse_args()

    if args.quantity < 1:
        parser.error("--quantity must be at least 1")

    load_dotenv(REPO_ROOT / ".env")

    try:
        detail = _run(
            InstrumentId.from_str(args.instrument),
            OrderSide[args.side],
            args.quantity,
            confirm=args.confirm,
        )
    except GateRefusedError as exc:
        refusal = getattr(exc, "refusal", None)
        reason = getattr(refusal, "reason", None) or "unknown"
        print(f"RESULT: fail reason=gate_refused refusal={reason}", flush=True)
        return 1
    except LiveNodeConfigError as exc:
        print(f"RESULT: fail reason=config_error msg={exc}", flush=True)
        return 1
    except FlattenError as exc:
        print(f"RESULT: fail reason=FlattenError msg={exc}", flush=True)
        return 1
    except KeyboardInterrupt:
        print("RESULT: fail reason=interrupted", flush=True)
        return 130
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        print(f"RESULT: fail reason={type(exc).__name__} msg={exc}", flush=True)
        return 1

    print(f"RESULT: ok mode=flatten {detail}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
