"""Component tests for SMA strategy behavior using test doubles.

These tests verify that SMA trading strategy correctly submits orders based on
crossover signals, using lightweight test doubles instead of the real Nautilus
BacktestEngine.

Purpose: Test strategy behavior without framework overhead
Reference: design.md Section 2.3 - Component Tests Using Test Doubles
"""

from decimal import Decimal

import pytest

from src.core.sma_logic import CrossoverSignal, SMATradingLogic
from tests.component.doubles import TestOrder, TestTradingEngine


@pytest.mark.component
class TestSMAStrategyWithTestDoubles:
    """Test SMA strategy behavior using test doubles."""

    def test_strategy_submits_buy_on_golden_cross(self):
        """Test strategy submits buy order when fast SMA crosses above slow SMA."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # Simulate golden cross: fast crosses above slow
        signal = logic.detect_crossover(
            prev_fast=Decimal("98"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("105"),
            curr_slow=Decimal("100"),
        )

        # Strategy should generate buy signal
        if signal == CrossoverSignal.GOLDEN_CROSS:
            order = TestOrder(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.1"),
                order_type="MARKET",
            )
            engine.submit_order(order)

        # Verify order submission
        assert len(engine.submitted_orders) == 1
        assert engine.submitted_orders[0].side == "BUY"
        assert engine.submitted_orders[0].status == "FILLED"
        assert engine.get_position("BTCUSDT") == Decimal("0.1")

    def test_strategy_submits_sell_on_death_cross(self):
        """Test strategy submits sell order when fast SMA crosses below slow SMA."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # First establish a long position
        buy_order = TestOrder("BTCUSDT", "BUY", Decimal("0.1"))
        engine.submit_order(buy_order)

        # Simulate death cross: fast crosses below slow
        signal = logic.detect_crossover(
            prev_fast=Decimal("102"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("95"),
            curr_slow=Decimal("100"),
        )

        # Strategy should generate sell signal
        if signal == CrossoverSignal.DEATH_CROSS:
            sell_order = TestOrder(
                symbol="BTCUSDT",
                side="SELL",
                quantity=Decimal("0.1"),
                order_type="MARKET",
            )
            engine.submit_order(sell_order)

        # Verify sell order submission
        assert len(engine.submitted_orders) == 2
        assert engine.submitted_orders[1].side == "SELL"
        assert engine.submitted_orders[1].status == "FILLED"
        assert engine.get_position("BTCUSDT") == Decimal("0")  # Position closed

    def test_strategy_no_action_on_no_crossover(self):
        """Test strategy does not submit orders when no crossover occurs."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # Simulate no crossover: fast remains above slow
        signal = logic.detect_crossover(
            prev_fast=Decimal("105"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("110"),
            curr_slow=Decimal("100"),
        )

        # Strategy should not generate any signal
        assert signal == CrossoverSignal.NO_CROSS

        # No orders should be submitted
        assert len(engine.submitted_orders) == 0

    def test_strategy_calculates_position_size_with_risk(self):
        """Test strategy calculates position size based on risk parameters."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # Calculate position size with 2% risk
        account_balance = engine.get_balance()
        position_size = logic.calculate_position_size(
            account_balance=account_balance,
            risk_percent=Decimal("0.02"),  # 2% risk
            entry_price=Decimal("50000"),
            stop_price=Decimal("49000"),  # $1000 risk per unit
        )

        # Risk: 10000 * 0.02 = 200
        # Price risk: 50000 - 49000 = 1000
        # Position size: 200 / 1000 = 0.2
        assert position_size == Decimal("0.2")

        # Submit order with calculated size
        order = TestOrder("BTCUSDT", "BUY", position_size)
        engine.submit_order(order)

        assert engine.get_position("BTCUSDT") == Decimal("0.2")

    def test_strategy_uses_entry_logic_for_long(self):
        """Test strategy uses entry logic to determine long entries."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # Fast SMA above slow SMA = long entry condition
        should_enter = logic.should_enter_long(fast_sma=Decimal("105"), slow_sma=Decimal("100"))

        assert should_enter is True

        # Submit order based on entry signal
        if should_enter:
            order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
            engine.submit_order(order)

        assert len(engine.submitted_orders) == 1
        assert engine.get_position("BTCUSDT") == Decimal("0.5")

    def test_strategy_uses_entry_logic_for_exits(self):
        """Test strategy uses entry logic to determine when to exit positions."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # Establish long position first
        buy_order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
        engine.submit_order(buy_order)

        # Fast SMA below slow SMA = short entry condition (exit long)
        # When fast < slow, we should exit long positions
        should_enter_short = logic.should_enter_short(
            fast_sma=Decimal("95"), slow_sma=Decimal("100")
        )

        assert should_enter_short is True

        # Exit long position when short signal appears
        if should_enter_short:
            sell_order = TestOrder("BTCUSDT", "SELL", Decimal("0.5"))
            engine.submit_order(sell_order)

        assert engine.get_position("BTCUSDT") == Decimal("0")  # Position closed

    def test_strategy_handles_multiple_instruments(self):
        """Test strategy can manage multiple instruments independently."""
        engine = TestTradingEngine(initial_balance=Decimal("20000"))
        logic = SMATradingLogic(fast_period=5, slow_period=20)

        # BTC golden cross
        btc_signal = logic.detect_crossover(
            prev_fast=Decimal("98"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("105"),
            curr_slow=Decimal("100"),
        )

        if btc_signal == CrossoverSignal.GOLDEN_CROSS:
            engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.1")))

        # ETH golden cross
        eth_signal = logic.detect_crossover(
            prev_fast=Decimal("2980"),
            prev_slow=Decimal("3000"),
            curr_fast=Decimal("3100"),
            curr_slow=Decimal("3000"),
        )

        if eth_signal == CrossoverSignal.GOLDEN_CROSS:
            engine.submit_order(TestOrder("ETHUSDT", "BUY", Decimal("1.0")))

        # Verify both positions
        assert engine.get_position("BTCUSDT") == Decimal("0.1")
        assert engine.get_position("ETHUSDT") == Decimal("1.0")
        assert len(engine.submitted_orders) == 2

    def test_strategy_event_log_tracks_actions(self):
        """Test that test engine logs all strategy actions."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Submit multiple orders
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        engine.submit_order(TestOrder("ETHUSDT", "BUY", Decimal("1.0")))

        # Verify event log captures all actions
        assert len(engine.event_log) > 0
        assert any("BTCUSDT" in event for event in engine.event_log)
        assert any("ETHUSDT" in event for event in engine.event_log)
        # Check for ORDER_SUBMITTED prefix in events
        submitted_count = sum(
            1 for event in engine.event_log if event.startswith("ORDER_SUBMITTED")
        )
        filled_count = sum(1 for event in engine.event_log if event.startswith("ORDER_FILLED"))
        assert submitted_count >= 2
        assert filled_count >= 2
