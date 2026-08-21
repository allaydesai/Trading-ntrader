"""An inert ``Controller`` whose only job is to unlock post-start registration.

Owns: :class:`SessionController`, :class:`SessionControllerConfig`, and
:func:`build_session_controller_config` — the ``ImportableControllerConfig`` the
runner hands to ``build_trading_node``.

Does not own: the startup sequence (``src/core/live_session_runner.py``), node
assembly (``src/core/live_node_builder.py``), or anything a controller
conventionally does. **This controller does nothing at all**, and that is the
design rather than an omission.

Why it exists, in one measured fact. ``Trader.add_strategy`` and
``Trader.add_actor`` **silently return** on a running trader unless the trader
was built with ``has_controller=True`` (``trading/trader.py:331-333`` and
``:395-397``):

.. code-block:: python

    if self.is_running and not self._has_controller:
        self._log.error("Cannot add a strategy to a running trader")
        return          # <-- silent return; NO exception

``has_controller`` is ``self._config.controller is not None``
(``system/kernel.py:480``). Story 2.5's whole design for AC #8 — build the node
with **zero** strategies, run ``gate:account`` against a trader holding
nothing, and register the strategies and the bar observer only after the gate
permits — depends on those two calls working on a *running* trader. Without a
controller they would fail with one ERROR log line and no exception, and the
session would run a full trading day having registered nothing.

Why the controller is empty rather than doing the work itself
(*Judgment call #5*): ``Controller.on_start()`` is synchronous, while
``verify_connected_account`` is a coroutine that must leave a refused node
genuinely down before it returns. Keeping the controller inert keeps every
phase in one readable sequence in one file, which is what AR39's "agents must
not reorder, merge, or skip" is trying to protect.

Known, accepted limit: a controller is a *capability*, not a guard. Setting one
means ``add_strategy`` on a running trader now succeeds — including from code
that had no business calling it. The ordering AC #8 requires is a property of
the runner's control flow, not of this class.
"""

from nautilus_trader.config import ControllerConfig, ImportableControllerConfig
from nautilus_trader.trading.controller import Controller

#: Dotted paths the Nautilus kernel resolves when it instantiates the
#: controller (``ControllerFactory.create``, ``live/config.py:256-266``). Kept
#: as constants so the unit tier can assert they still resolve, rather than
#: discovering a typo while a node is being built against a live gateway —
#: the same treatment ``live_node_builder`` gives the bar observer's paths.
SESSION_CONTROLLER_PATH = "src.core.live_session_controller:SessionController"
SESSION_CONTROLLER_CONFIG_PATH = "src.core.live_session_controller:SessionControllerConfig"


class SessionControllerConfig(ControllerConfig, frozen=True):
    """Configuration for :class:`SessionController` — deliberately empty.

    A distinct class rather than ``ControllerConfig`` itself because
    ``ControllerFactory.create`` resolves ``config_path`` and calls
    ``.parse()`` on whatever it finds; naming this repo's own type keeps the
    two dotted paths in one module and makes a future field addition a local
    edit rather than a change to a third-party class's meaning.
    """


class SessionController(Controller):
    """A controller that does nothing, so that registration is not refused.

    ``Controller.__init__(self, trader, config=None)`` is inherited unchanged —
    ``ControllerFactory.create`` passes both by keyword.
    """

    def on_start(self) -> None:
        """Nothing. See the module docstring for why this is empty."""

    def on_stop(self) -> None:
        """Nothing. Teardown is the runner's, in its ``finally`` (AR38)."""


def build_session_controller_config() -> ImportableControllerConfig:
    """Build the importable config that sets ``has_controller`` on the trader.

    Returns:
        An ``ImportableControllerConfig`` naming this module's two classes.
        ``config`` is ``{}`` and **must** be passed: the field is required, and
        ``ControllerFactory.create`` feeds it straight to
        ``msgspec.json.encode``, where ``None`` would not parse into
        :class:`SessionControllerConfig`.
    """
    return ImportableControllerConfig(
        controller_path=SESSION_CONTROLLER_PATH,
        config_path=SESSION_CONTROLLER_CONFIG_PATH,
        config={},
    )
