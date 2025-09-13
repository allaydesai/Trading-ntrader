"""Minimal backtest engine wrapper for Nautilus Trader."""

from decimal import Decimal
from typing import Dict, Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money

from src.core.strategies.sma_crossover import SMACrossover, SMAConfig
from src.utils.mock_data import create_test_instrument, generate_mock_bars
from src.config import get_settings


class BacktestResult:
    """Simple container for backtest results."""

    def __init__(
        self,
        total_return: float = 0.0,
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        largest_win: float = 0.0,
        largest_loss: float = 0.0,
        final_balance: float = 0.0,
    ):
        """Initialize backtest results."""
        self.total_return = total_return
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.losing_trades = losing_trades
        self.largest_win = largest_win
        self.largest_loss = largest_loss
        self.final_balance = final_balance

    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    def __str__(self) -> str:
        """String representation of results."""
        return (
            f"BacktestResult(total_return={self.total_return:.2f}, "
            f"total_trades={self.total_trades}, win_rate={self.win_rate:.1f}%)"
        )


class MinimalBacktestRunner:
    """Minimal backtest runner using Nautilus Trader."""

    def __init__(self):
        """Initialize the backtest runner."""
        self.settings = get_settings()
        self.engine: BacktestEngine | None = None
        self._results: BacktestResult | None = None

    def run_sma_backtest(
        self,
        fast_period: int = None,
        slow_period: int = None,
        trade_size: Decimal = None,
        num_bars: int = None,
    ) -> BacktestResult:
        """
        Run a simple SMA crossover backtest with mock data.

        Parameters
        ----------
        fast_period : int, optional
            Fast SMA period. Defaults to config value.
        slow_period : int, optional
            Slow SMA period. Defaults to config value.
        trade_size : Decimal, optional
            Trade size. Defaults to config value.
        num_bars : int, optional
            Number of mock data bars. Defaults to config value.

        Returns
        -------
        BacktestResult
            Results of the backtest.
        """
        # Use config defaults if not specified
        if fast_period is None:
            fast_period = self.settings.fast_ema_period
        if slow_period is None:
            slow_period = self.settings.slow_ema_period
        if trade_size is None:
            trade_size = self.settings.trade_size
        if num_bars is None:
            num_bars = self.settings.mock_data_bars

        # Create test instrument
        instrument, instrument_id = create_test_instrument()

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=1.0,  # Always fill limit orders when price touches
            prob_slippage=0.0,  # No slippage for initial testing
        )

        # Add venue
        venue = Venue("SIM")
        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(self.settings.default_balance, USD)],
            fill_model=fill_model,
        )

        # Add instrument
        self.engine.add_instrument(instrument)

        # Generate mock data
        # Must match the bar type created in generate_mock_bars
        bar_type_str = f"{instrument_id}-15-MINUTE-MID-EXTERNAL"
        # Create bar type for strategy configuration
        BarType.from_str(bar_type_str)  # Validate format
        bars = generate_mock_bars(instrument_id, num_bars=num_bars)

        # Add bars to engine
        self.engine.add_data(bars)

        # Configure strategy
        strategy_config = SMAConfig(
            instrument_id=instrument_id,
            bar_type=bar_type_str,
            fast_period=fast_period,
            slow_period=slow_period,
            trade_size=trade_size,
        )

        # Create and add strategy
        strategy = SMACrossover(config=strategy_config)
        self.engine.add_strategy(strategy=strategy)

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    def _extract_results(self) -> BacktestResult:
        """
        Extract results from the backtest engine.

        Returns
        -------
        BacktestResult
            Extracted results.
        """
        if not self.engine:
            return BacktestResult()

        # Get account for analysis
        venue = Venue("SIM")
        account = self.engine.cache.account_for_venue(venue)

        if not account:
            return BacktestResult()

        # Calculate basic metrics
        starting_balance = float(self.settings.default_balance)
        final_balance = float(account.balance_total(USD).as_double())
        total_return = final_balance - starting_balance

        # Get trade statistics
        closed_positions = self.engine.cache.positions_closed()
        total_trades = len(closed_positions)

        winning_trades = 0
        losing_trades = 0
        largest_win = 0.0
        largest_loss = 0.0

        for position in closed_positions:
            # Use realized PnL for closed positions
            pnl = (
                position.realized_pnl.as_double()
                if hasattr(position, "realized_pnl")
                else 0.0
            )
            if pnl > 0:
                winning_trades += 1
                largest_win = max(largest_win, pnl)
            else:
                losing_trades += 1
                largest_loss = min(largest_loss, pnl)

        return BacktestResult(
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            largest_win=largest_win,
            largest_loss=largest_loss,
            final_balance=final_balance,
        )

    def get_detailed_results(self) -> Dict[str, Any]:
        """
        Get detailed results from the last backtest run.

        Returns
        -------
        Dict[str, Any]
            Detailed results dictionary.
        """
        if not self.engine or not self._results:
            return {}

        # Future: Could get account for detailed analysis via Venue("SIM")

        return {
            "basic_metrics": {
                "total_return": self._results.total_return,
                "total_trades": self._results.total_trades,
                "win_rate": self._results.win_rate,
                "largest_win": self._results.largest_win,
                "largest_loss": self._results.largest_loss,
                "final_balance": self._results.final_balance,
            },
            "account_summary": {
                "starting_balance": float(self.settings.default_balance),
                "final_balance": self._results.final_balance,
                "currency": "USD",
            },
            "positions": len(self.engine.cache.positions_closed()),
            "orders": len(self.engine.cache.orders()),
        }

    def reset(self) -> None:
        """Reset the engine for a new backtest."""
        if self.engine:
            self.engine.reset()
        self._results = None

    def dispose(self) -> None:
        """Dispose of the engine resources."""
        if self.engine:
            self.engine.dispose()
        self._results = None
