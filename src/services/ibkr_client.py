"""IBKR client wrapper for historical data fetching."""

import asyncio
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict

import structlog  # noqa: E402
from ibapi.common import MarketDataTypeEnum  # type: ignore
from nautilus_trader.adapters.interactive_brokers.historical.client import (
    HistoricInteractiveBrokersClient,
)
from nautilus_trader.common import component as _nautilus_component
from nautilus_trader.model.identifiers import InstrumentId

from src.utils.logging import get_nautilus_log_guard, set_nautilus_log_guard

logger = structlog.get_logger(__name__)


@contextmanager
def _guard_nautilus_logging():
    """Patch init_logging to return existing guard if logging is already initialized.

    HistoricInteractiveBrokersClient unconditionally calls init_logging() in its
    __init__. When the web server has already initialized Nautilus logging, this
    causes "Logging subsystem already initialized". This context manager temporarily
    replaces init_logging with a no-op that returns the existing LogGuard.
    """
    if _nautilus_component.is_logging_initialized():
        existing_guard = get_nautilus_log_guard()
        original_init = _nautilus_component.init_logging

        def _noop_init_logging(**kwargs):
            return existing_guard

        _nautilus_component.init_logging = _noop_init_logging
        # Also patch the module-level reference used by the IBKR client
        import nautilus_trader.adapters.interactive_brokers.historical.client as _ibkr_mod

        _ibkr_mod.init_logging = _noop_init_logging
        try:
            yield
        finally:
            _nautilus_component.init_logging = original_init
            _ibkr_mod.init_logging = original_init
    else:
        yield


class RateLimiter:
    """
    Rate limiting for IBKR API calls.

    IBKR enforces 50 requests/second. This implementation uses a conservative
    limit of 45 req/sec (90% of limit) for safety.

    Reference: milestone-5-design.md:406-458 (Rate Limiting Strategy)
    """

    def __init__(self, requests_per_second: int = 45):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests allowed per second
        """
        self.requests_per_second = requests_per_second
        self.window = timedelta(seconds=1)
        self.requests: deque[datetime] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> datetime:
        """
        Wait until a request slot is available.

        Implements sliding window rate limiting with thread-safe operations.
        Uses asyncio.Lock to prevent race conditions in concurrent scenarios.

        Returns:
            The timestamp when this request was recorded
        """
        async with self._lock:
            while True:
                now = datetime.now(timezone.utc)

                # Remove expired requests outside the current window
                while self.requests and self.requests[0] < now - self.window:
                    self.requests.popleft()

                # If we have capacity, record this request and return
                if len(self.requests) < self.requests_per_second:
                    self.requests.append(now)
                    return now

                # At limit, wait until oldest request expires
                sleep_time = (self.requests[0] + self.window - now).total_seconds()
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                # Loop back to re-check after sleeping


class IBKRHistoricalClient:
    """
    Wrapper around Nautilus HistoricInteractiveBrokersClient.

    Provides simplified interface for backtesting data retrieval with
    built-in rate limiting and connection management.

    Reference: milestone-5-design.md:72-155 (Historical Data Client Setup)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        market_data_type: MarketDataTypeEnum = MarketDataTypeEnum.DELAYED_FROZEN,
    ):
        """
        Initialize IBKR historical data client.

        Args:
            host: IB Gateway/TWS host address
            port: Connection port (7497=TWS paper, 7496=TWS live,
                  4002=Gateway paper, 4001=Gateway live)
            client_id: Unique client identifier; rotated automatically on
                  reconnect if the Gateway holds a stale session for it.
            market_data_type: Data type (DELAYED_FROZEN for paper trading)
        """
        # Reason: store params so we can rebuild the underlying client with
        # a rotated client_id when the Gateway holds a stale session.
        self._host = host
        self._port = port
        self._base_client_id = client_id
        self._active_client_id = client_id
        self._market_data_type = market_data_type

        self._build_inner_client(client_id=client_id)
        self._connected = False
        self.rate_limiter = RateLimiter(requests_per_second=45)

    def _build_inner_client(self, *, client_id: int) -> None:
        """Construct (or reconstruct) the underlying Nautilus client.

        Called from ``__init__`` and again from ``connect()`` when the
        previous client_id timed out (likely IBKR error 326 — the Gateway
        still holds the id from a SIGKILL'd prior process).

        Preserves the LogGuard returned by ``HistoricInteractiveBrokersClient``
        so the Nautilus logging subsystem stays initialised across rebuilds
        (CLAUDE.md Gotcha #1).
        """
        with _guard_nautilus_logging():
            self.client = HistoricInteractiveBrokersClient(
                host=self._host,
                port=self._port,
                client_id=client_id,
                market_data_type=self._market_data_type,
                log_level="INFO",
            )
        if hasattr(self.client, "_log_guard"):
            set_nautilus_log_guard(self.client._log_guard)
        self._active_client_id = client_id

    async def _stop_inner(self) -> None:
        """Best-effort graceful stop of the underlying Nautilus client.

        Calls ``InteractiveBrokersClient._stop_async`` which cancels
        Nautilus's internal tasks and invokes ``EClient.disconnect()``,
        releasing the client_id on the Gateway side. Errors are logged
        but never raised — this is called from teardown paths where
        making things worse on failure is unhelpful.
        """
        inner = getattr(self.client, "_client", None)
        if inner is None:
            return
        try:
            await inner._stop_async()
            # Reason: give the socket close a beat to flush before any
            # subsequent rebuild reuses the loop / port.
            await asyncio.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ibkr_inner_stop_failed", error=str(exc))

    async def connect(self, timeout: int = 30, max_id_rotations: int = 5) -> Dict:
        """
        Establish connection to IBKR Gateway with automatic client_id rotation.

        IBKR Gateway error 326 ("client id is already in use") is logged
        by the underlying Nautilus client but is *not* raised — the connect
        call simply times out waiting for ``_is_client_ready``. After a
        SIGKILL'd previous run, the Gateway holds the configured client_id
        for tens of seconds, so reconnects with the same id silently time
        out and force a Gateway restart.

        To recover automatically, a connect timeout is treated as a likely
        id conflict: the underlying client is torn down and rebuilt with
        ``base_client_id + offset`` for offset in ``1..max_id_rotations``.

        Args:
            timeout: Per-attempt connection timeout in seconds.
            max_id_rotations: Maximum number of rotated client_ids to try
                after the configured one fails. ``0`` disables rotation
                (legacy behaviour). Default is ``5`` — covers the typical
                "two recent kills, plus headroom" case.

        Returns:
            Connection info dict including ``client_id`` (the id that
            actually succeeded — may differ from the configured one).

        Raises:
            ConnectionError: If all ``max_id_rotations + 1`` attempts fail.
        """
        last_error: BaseException | None = None
        for offset in range(max_id_rotations + 1):
            candidate_id = self._base_client_id + offset
            if offset > 0:
                logger.warning(
                    "ibkr_connect_rotating_client_id",
                    base_id=self._base_client_id,
                    candidate_id=candidate_id,
                    attempt=offset,
                    max_attempts=max_id_rotations,
                )
                await self._stop_inner()
                try:
                    self._build_inner_client(client_id=candidate_id)
                except Exception as build_exc:  # noqa: BLE001
                    # Rebuild itself failed — surface clearly rather than
                    # leaving ``self.client`` half-built for the next attempt.
                    raise ConnectionError(
                        f"Failed to rebuild IBKR client during rotation "
                        f"(candidate_id={candidate_id}): {build_exc}"
                    ) from build_exc

            try:
                await asyncio.wait_for(self.client.connect(), timeout=timeout)
                self._connected = True
                return {
                    "connected": True,
                    "account_id": getattr(self.client, "account_id", "N/A"),
                    "server_version": getattr(self.client, "server_version", "N/A"),
                    "connection_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "client_id": candidate_id,
                }
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(
                    "ibkr_connect_timeout",
                    client_id=candidate_id,
                    attempt=offset,
                )
                continue
            except Exception as e:
                # Reason: non-timeout errors (refused, generic) aren't id-conflict
                # symptoms, so don't waste time rotating — surface immediately.
                raise ConnectionError(f"Failed to connect to IBKR: {e}") from e

        raise ConnectionError(
            f"Failed to connect to IBKR after {max_id_rotations + 1} client_id "
            f"rotations (base={self._base_client_id}). The Gateway may be holding "
            f"stale sessions from a previously killed process; wait a minute and "
            f"retry, or restart the Gateway. If a concurrent harness is running "
            f"with the same base client_id (rotation is deterministic — "
            f"base+1, base+2, ...), use a disjoint base id to avoid cross-process "
            f"contention. Last error: {last_error}"
        )

    async def disconnect(self):
        """Gracefully disconnect from IBKR Gateway.

        Calls ``_stop_async`` on the underlying Nautilus client which
        cancels its internal tasks and invokes ``EClient.disconnect()``,
        releasing the client_id on the Gateway side. Without this, an
        abrupt process exit leaves the Gateway holding the id and the
        next run hits IBKR error 326 (mitigated by client_id rotation
        in ``connect()``, but graceful release avoids the rotation
        entirely).
        """
        if self._connected:
            await self._stop_inner()
            self._connected = False

    async def fetch_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-MINUTE-LAST",
    ):
        """
        Fetch historical bars and instrument from IBKR.

        Args:
            instrument_id: Instrument ID (e.g., "AAPL.NASDAQ")
            start: Start datetime (UTC)
            end: End datetime (UTC)
            bar_type_spec: Bar type specification (e.g., "1-MINUTE-LAST")

        Returns:
            Tuple of (bars, instrument) where bars is a list of Bar objects
            and instrument is the Instrument object

        Raises:
            Exception: If fetch fails
        """
        # Reason: Apply rate limiting before request
        await self.rate_limiter.acquire()

        # Reason: Parse instrument_id to get symbol and venue
        # Expected format: "SYMBOL.VENUE" (e.g., "AAPL.NASDAQ")
        parts = instrument_id.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid instrument_id format: {instrument_id}")

        symbol, venue = parts

        # Reason: Validate bar type spec format
        # Expected format: "{period}-{aggregation}-{price_type}"
        # Example: "1-MINUTE-LAST"
        bar_parts = bar_type_spec.split("-")
        if len(bar_parts) < 2:
            raise ValueError(f"Invalid bar_type_spec format: {bar_type_spec}")

        # Reason: Strip timezone info since we're specifying tz_name parameter
        # Nautilus expects naive datetimes when tz_name is provided
        start_naive = start.replace(tzinfo=None) if start.tzinfo else start
        end_naive = end.replace(tzinfo=None) if end.tzinfo else end

        # Reason: Resolve instrument first to get the correct exchange.
        # IBKR qualifies contracts to their primary exchange (e.g., GDX.NASDAQ
        # resolves to GDX.ARCA). We must use the resolved ID for the bars
        # request, otherwise Nautilus can't find the instrument in its cache.
        nautilus_instrument_id = InstrumentId.from_str(instrument_id)
        instruments = await self.client.request_instruments(
            instrument_ids=[nautilus_instrument_id],
        )
        instrument = instruments[0] if instruments else None

        # Use the resolved instrument ID for bars request if IBKR qualified
        # the contract to a different exchange
        resolved_id = str(instrument.id) if instrument else instrument_id
        if resolved_id != instrument_id:
            logger.info(
                "ibkr_instrument_resolved",
                requested=instrument_id,
                resolved=resolved_id,
            )

        # Reason: Request bars from IBKR via Nautilus client
        # bar_specifications should be simple format strings like "1-MINUTE-LAST"
        # Use instrument_ids instead of contracts to avoid parsing issues
        bars = await self.client.request_bars(
            bar_specifications=[bar_type_spec],  # Just "1-MINUTE-LAST"
            end_date_time=end_naive,  # Required parameter (comes before start!)
            tz_name="UTC",
            start_date_time=start_naive,  # Optional start time
            instrument_ids=[resolved_id],  # Use resolved ID from IBKR
            use_rth=True,  # Regular Trading Hours only
            timeout=120,  # 2 minute timeout
        )

        return bars, instrument

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected
