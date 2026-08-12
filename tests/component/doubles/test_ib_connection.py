"""Test doubles for the Interactive Brokers client's connection surface.

At nautilus-trader 1.220.0 a dropped IBKR socket publishes no message-bus event
and changes no public connection property, so the only in-process truth about
liveness is a pair of ``asyncio.Event`` flags the adapter's client keeps
(``adapters/interactive_brokers/client/client.py:122-123``):

- ``_is_ib_connected`` — set once TWS/Gateway returns ``managedAccounts``,
  cleared on every disconnect path.
- ``_is_client_ready`` — cleared by the client's ``_degrade()`` on connection
  loss, set again only after the reconnect handshake completes.

The reader also consults ``is_running`` / ``is_disposed`` (inherited from
Nautilus's ``Component``) to reject a corpse left in the never-purged
``IB_CLIENTS`` cache, so the doubles carry those too.

These doubles deliberately do not use real ``asyncio.Event`` objects: the
reader only calls ``is_set()``, and a stub keeps them loop-free so component
tests need no event loop to flip a connection on and off.

Every class here defines ``__init__`` — including the degenerate ones — so
pytest declines to collect them as test classes rather than leaving that to the
accident of having no ``test_``-prefixed methods.

Design Pattern: Test Double (Fake)
Purpose: Exercise connection-loss detection without a broker (NFR32)
"""


class FlagStub:
    """The slice of ``asyncio.Event`` the connection reader actually touches."""

    def __init__(self, initial: bool = False) -> None:
        self._set = initial

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def clear(self) -> None:
        self._set = False


class RaisingFlagStub:
    """A flag whose ``is_set()`` blows up.

    Stands in for a cached object that satisfies ``getattr`` but not the
    protocol — the shape that made the reader's fail-closed guard necessary in
    the first place.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("flag is not readable")

    def is_set(self) -> bool:
        raise self._error


class TestIBConnection:
    """Lightweight stand-in for ``InteractiveBrokersClient``'s connection state.

    Attributes:
        _is_ib_connected: Socket-level liveness, as the adapter tracks it.
        _is_client_ready: Whether the adapter has finished its post-connect
            handshake. Named with the adapter's own leading underscore on
            purpose — the reader looks these up by name, so a double that
            renamed them would test nothing.
        is_running: Component lifecycle, as the reader checks it.
        is_disposed: Component lifecycle, as the reader checks it.

    Example:
        >>> client = TestIBConnection()
        >>> client.drop_connection()
        >>> client._is_ib_connected.is_set()
        False
    """

    def __init__(
        self,
        *,
        socket_connected: bool = True,
        client_ready: bool = True,
        is_running: bool = True,
        is_disposed: bool = False,
    ) -> None:
        self._is_ib_connected = FlagStub(socket_connected)
        self._is_client_ready = FlagStub(client_ready)
        self.is_running = is_running
        self.is_disposed = is_disposed

    def drop_connection(self) -> None:
        """Mimic a socket drop: the watchdog clears both flags."""
        self._is_ib_connected.clear()
        self._is_client_ready.clear()

    def restore_connection(self) -> None:
        """Mimic a completed reconnect handshake."""
        self._is_ib_connected.set()
        self._is_client_ready.set()


class TestIBConnectionWithoutFlags:
    """A cached client that exposes neither connection flag.

    Stands in for what a Nautilus upgrade that renamed the private flags would
    look like from the reader's side. The reader must report "disconnected"
    rather than raising ``AttributeError`` — withholding permission is the safe
    direction to be wrong in, and taking the session down is not.
    """

    def __init__(self, *, is_running: bool = True, is_disposed: bool = False) -> None:
        self.is_running = is_running
        self.is_disposed = is_disposed


class TestIBConnectionWithPlainFlags:
    """A cached client whose flags are bare booleans rather than events.

    This is what a Nautilus refactor from ``asyncio.Event`` to a plain ``bool``
    would look like: ``getattr`` succeeds, so the reader's ``None`` guard does
    not fire, and ``True.is_set`` raises ``AttributeError`` inside the guard.
    Without a double of this shape the fail-closed ``except`` arm has no
    coverage at all — the whole guard could be deleted and the suite stay green.
    """

    def __init__(self, *, socket_connected: bool = True, client_ready: bool = True) -> None:
        self._is_ib_connected = socket_connected
        self._is_client_ready = client_ready
        self.is_running = True
        self.is_disposed = False


class TestIBConnectionWithRaisingFlags:
    """A cached client whose flag *reads* raise, not just whose calls do.

    ``_is_ib_connected`` is a property that raises, which ``getattr``'s default
    cannot absorb — only ``AttributeError`` is swallowed there. This is the case
    that forced the attribute read itself inside the reader's try block.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("attribute is not readable")
        self.is_running = True
        self.is_disposed = False

    @property
    def _is_ib_connected(self) -> object:
        raise self._error

    @property
    def _is_client_ready(self) -> object:
        raise self._error
