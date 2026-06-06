"""IBKR Gateway reconnect/disconnect diagnostic probe (Story 3.4).

Characterises the kill-then-reconnect failure mode hit by Run 1 of the
Story 3.4 parity harness. Each mode produces a single-line ``RESULT:``
report so it's easy to script multi-step scenarios.

Usage::

    uv run python scripts/diagnostics/ibkr_reconnect_probe.py --mode <mode> --client-id <id>

Modes:
    clean             Connect, sleep 2s, disconnect via _stop_async, exit. Baseline.
    no-stop           Connect, sleep 2s, exit WITHOUT calling _stop_async.
                      Simulates the production harness's current shutdown path.
    connect-only      Connect, sleep 60s, exit. Designed to be SIGKILL'd externally
                      by an orchestrating script.
    fetch-then-hang   Connect, start a long-running ``request_bars`` (AAPL Jan 2018
                      1-MIN), then sleep 60s after it completes. Designed to be
                      SIGKILL'd mid-fetch — the closest reproduction of the
                      Story 3.4 Run 1 ``pkill`` symptom.
    handshake-time    Time only the connect handshake. Used to baseline the
                      Gateway after a fresh restart.

Each mode prints a final ``RESULT: ok|fail|timeout reason=<...> elapsed=<...>``
line to stdout for easy parsing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import structlog
from dotenv import load_dotenv

load_dotenv()

from src.services.ibkr_client import IBKRHistoricalClient  # noqa: E402

logger = structlog.get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).split("#")[0].strip()
    return int(raw)


async def _connect(client_id: int, *, timeout: int = 15) -> tuple[IBKRHistoricalClient, float]:
    host = os.environ.get("IBKR_HOST", "127.0.0.1").split("#")[0].strip()
    port = _env_int("IBKR_PORT", 4002)

    print(f"[probe] connecting host={host} port={port} client_id={client_id}", flush=True)
    t0 = time.monotonic()
    client = IBKRHistoricalClient(host=host, port=port, client_id=client_id)
    await asyncio.wait_for(client.connect(timeout=timeout), timeout=timeout + 5)
    elapsed = time.monotonic() - t0
    print(f"[probe] connected in {elapsed:.2f}s", flush=True)
    return client, elapsed


async def _stop_underlying(client: IBKRHistoricalClient) -> None:
    """Call the real disconnect path on the wrapped Nautilus client.

    The wrapper's own ``disconnect`` is currently a no-op (see ibkr_client.py).
    The Nautilus client exposes ``_stop_async`` which cancels its internal
    tasks and calls ``self._eclient.disconnect()``.
    """
    inner = getattr(client.client, "_client", None)
    if inner is None:
        print("[probe] no inner _client available, skipping graceful stop", flush=True)
        return
    print("[probe] calling _stop_async on inner Nautilus client", flush=True)
    await inner._stop_async()
    # Give socket close a beat to flush
    await asyncio.sleep(0.5)


async def _run(mode: str, client_id: int) -> int:
    if mode == "clean":
        client, _ = await _connect(client_id)
        await asyncio.sleep(2)
        await _stop_underlying(client)
        print("RESULT: ok mode=clean", flush=True)
        return 0

    if mode == "no-stop":
        client, _ = await _connect(client_id)
        await asyncio.sleep(2)
        # Intentionally exit without disconnecting — mirrors current harness.
        print("RESULT: ok mode=no-stop note=exited-without-disconnect", flush=True)
        return 0

    if mode == "connect-only":
        client, _ = await _connect(client_id)
        print(f"[probe] sleeping for 60s as PID={os.getpid()}", flush=True)
        await asyncio.sleep(60)
        print("RESULT: ok mode=connect-only", flush=True)
        return 0

    if mode == "fetch-then-hang":
        from datetime import datetime, timezone

        client, _ = await _connect(client_id)
        print(f"[probe] starting fetch as PID={os.getpid()}", flush=True)
        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
        bars, _ = await client.fetch_bars("AAPL.NASDAQ", start, end, "1-MINUTE-LAST")
        print(f"[probe] fetch returned {len(bars)} bars", flush=True)
        await asyncio.sleep(60)
        print("RESULT: ok mode=fetch-then-hang", flush=True)
        return 0

    if mode == "handshake-time":
        client, elapsed = await _connect(client_id)
        await _stop_underlying(client)
        print(f"RESULT: ok mode=handshake-time elapsed={elapsed:.3f}", flush=True)
        return 0

    print(f"RESULT: fail reason=unknown_mode mode={mode}", flush=True)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "clean",
            "no-stop",
            "connect-only",
            "fetch-then-hang",
            "handshake-time",
        ],
    )
    parser.add_argument("--client-id", type=int, default=_env_int("IBKR_CLIENT_ID", 10))
    args = parser.parse_args()

    t0 = time.monotonic()
    try:
        return asyncio.run(_run(args.mode, args.client_id))
    except asyncio.TimeoutError:
        print(
            f"RESULT: timeout mode={args.mode} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
    except Exception as e:
        print(
            f"RESULT: fail mode={args.mode} reason={type(e).__name__} "
            f"msg={e!s} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
