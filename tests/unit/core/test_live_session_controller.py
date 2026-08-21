"""Unit tests for the inert ``SessionController`` (Story 2.5, AC #8).

Unit tier despite the ``nautilus_trader.config`` import, on the precedent
``tests/unit/cli/commands/test_live_cli.py`` sets: nothing here constructs a
``TradingNode``, opens a socket, or initialises the Nautilus C logging
subsystem. Resolving two dotted paths and building a msgspec struct does none
of those — asserted below rather than assumed.

The controller exists for exactly one reason and does nothing else:
``Trader.add_strategy`` and ``Trader.add_actor`` **silently return** on a
running trader unless ``has_controller`` is set (``trading/trader.py:331-333``,
``:395-397``, fed from ``system/kernel.py:480``). Without it, Story 2.5's
"register the strategies *after* ``gate:account``" design would produce a
session that runs for a whole trading day having registered nothing, with one
ERROR log line and no exception.
"""

import pytest
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import ControllerConfig, ImportableControllerConfig
from nautilus_trader.live.config import ControllerFactory
from nautilus_trader.trading.controller import Controller

from src.core.live_session_controller import (
    SessionController,
    SessionControllerConfig,
    build_session_controller_config,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce this file's unit-tier placement."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this unit test changed the Nautilus C logging state — resolving a controller's "
        "dotted paths must not touch the C logging subsystem."
    )


class TestTheClassShapes:
    def test_the_controller_is_a_nautilus_controller(self):
        assert issubclass(SessionController, Controller)

    def test_the_config_is_a_nautilus_controller_config(self):
        assert issubclass(SessionControllerConfig, ControllerConfig)

    def test_the_config_adds_no_fields_of_its_own(self):
        """A field here would have to survive a msgspec JSON round trip through
        ``ImportableControllerConfig.config``; the controller needs none.
        """
        assert SessionControllerConfig.__struct_fields__ == ControllerConfig.__struct_fields__

    def test_the_controller_declares_no_behaviour_beyond_the_two_empty_hooks(self):
        """*Judgment call #5*: it exists to unlock a door, nothing more.

        An exact set, so a helpfully-added ``on_bar`` or a strategy-creating
        ``on_start`` body fails here rather than in production.
        """
        declared = {name for name in vars(SessionController) if not name.startswith("__")}
        assert declared == {"on_start", "on_stop"}

    def test_both_hooks_are_empty(self):
        import inspect

        for hook in (SessionController.on_start, SessionController.on_stop):
            body = inspect.getsource(hook)
            # A docstring and nothing else: no call of any kind.
            assert "(" not in body.split('"""')[-1].replace("\n", "")


class TestTheImportableConfig:
    """What ``ControllerFactory.create`` actually does with it."""

    def test_it_returns_an_importable_controller_config(self):
        assert isinstance(build_session_controller_config(), ImportableControllerConfig)

    def test_the_dotted_paths_name_this_modules_two_classes(self):
        config = build_session_controller_config()

        assert config.controller_path == "src.core.live_session_controller:SessionController"
        assert config.config_path == "src.core.live_session_controller:SessionControllerConfig"

    def test_the_config_payload_is_an_empty_dict_not_none(self):
        """``ImportableControllerConfig.config`` is a **required** field."""
        assert build_session_controller_config().config == {}

    def test_both_paths_resolve_through_the_functions_the_factory_uses(self):
        """``ControllerFactory.create`` calls exactly these two resolvers, so a
        typo in either dotted path fails here rather than mid-way through a
        live node build against a real gateway.
        """
        from nautilus_trader.common.config import resolve_config_path, resolve_path

        config = build_session_controller_config()

        assert resolve_path(config.controller_path) is SessionController
        assert resolve_config_path(config.config_path) is SessionControllerConfig

    def test_the_payload_parses_into_the_config_class_the_factory_will_build(self):
        """The last step of ``ControllerFactory.create`` before construction."""
        import msgspec

        config = build_session_controller_config()
        parsed = SessionControllerConfig.parse(msgspec.json.encode(config.config))

        assert isinstance(parsed, SessionControllerConfig)

    def test_the_factory_is_the_consumer_this_shape_is_built_for(self):
        """A guard against the shape drifting: if ``ControllerFactory.create``
        ever stops calling ``resolve_path``/``resolve_config_path``/``parse``,
        the three assertions above stop proving anything.
        """
        import inspect

        source = inspect.getsource(ControllerFactory.create)

        assert "resolve_path(config.controller_path)" in source
        assert "resolve_config_path(config.config_path)" in source
        assert "controller_cls(config=config, trader=trader)" in source
