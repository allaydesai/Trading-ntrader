"""Minimal backtest engine wrapper for Nautilus Trader."""

from decimal import Decimal
from typing import Dict, Any, Optional, Literal
from datetime import datetime

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money

from src.core.strategies.sma_crossover import SMACrossover, SMAConfig
from src.core.strategy_factory import StrategyFactory
from src.utils.mock_data import create_test_instrument, generate_mock_bars
from src.utils.config_loader import ConfigLoader, StrategyConfigWrapper
from src.services.data_service import DataService
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

    def __init__(self, data_source: Literal["mock", "database"] = "mock"):
        """
        Initialize the backtest runner.

        Args:
            data_source: Data source to use ('mock' or 'database')
        """
        self.settings = get_settings()
        self.data_source = data_source
        self.data_service = DataService() if data_source == "database" else None
        self.engine: BacktestEngine | None = None
        self._results: BacktestResult | None = None

    def run_sma_backtest(
        self,
        fast_period: Optional[int] = None,
        slow_period: Optional[int] = None,
        trade_size: Optional[Decimal] = None,
        num_bars: Optional[int] = None,
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
            prob_fill_on_limit=0.95,     # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,      # 95% fill probability on stop orders
            prob_slippage=0.01,          # 1% slippage probability
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

    async def run_backtest_with_database(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        fast_period: Optional[int] = None,
        slow_period: Optional[int] = None,
        trade_size: Optional[Decimal] = None,
    ) -> BacktestResult:
        """
        Run backtest using real data from database.

        Args:
            symbol: Trading symbol (e.g., AAPL)
            start: Start datetime for backtest
            end: End datetime for backtest
            fast_period: Fast SMA period
            slow_period: Slow SMA period
            trade_size: Trade size

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If data source is not 'database' or no data available
            ConnectionError: If database is not accessible
        """
        if self.data_source != "database":
            raise ValueError("Data source must be 'database' for this method")

        if not self.data_service:
            raise ValueError("Data service not initialized")

        # Use config defaults if not specified
        if fast_period is None:
            fast_period = self.settings.fast_ema_period
        if slow_period is None:
            slow_period = self.settings.slow_ema_period
        if trade_size is None:
            trade_size = self.settings.trade_size

        # Validate data availability
        validation = await self.data_service.validate_data_availability(
            symbol, start, end
        )
        if not validation["valid"]:
            raise ValueError(f"Data validation failed: {validation['reason']}")

        # Fetch data from database
        market_data = await self.data_service.get_market_data(symbol, start, end)

        if not market_data:
            raise ValueError(
                f"No market data found for {symbol} between {start} and {end}"
            )

        # Create test instrument for the actual symbol with SIM venue
        # This ensures the instrument matches the data being loaded
        if "/" in symbol and len(symbol.split("/")) == 2:
            # For FX pairs, create using the original symbol but force SIM venue
            base_instrument, _ = create_test_instrument(symbol)
            # The FX instrument should already have SIM venue from create_test_instrument
            instrument = base_instrument
        else:
            # For equity symbols, create with the actual symbol name for SIM venue
            clean_symbol = symbol.replace("2018", "18").replace("_", "")[:7]

            # Create using TestInstrumentProvider but override with correct symbol and SIM venue
            from nautilus_trader.test_kit.providers import TestInstrumentProvider

            try:
                # Use TestInstrumentProvider to create a properly configured equity with SIM venue
                # This is a simpler approach that maintains all the correct instrument properties
                instrument = TestInstrumentProvider.equity(
                    symbol=clean_symbol,
                    venue="SIM",  # venue parameter expects string, not Venue object
                )
            except Exception:
                # Fallback: create a generic instrument but with the correct symbol
                # Use FX template but override the symbol to maintain compatibility
                base_fx = TestInstrumentProvider.default_fx_ccy("EUR/USD")
                # For fallback, we'll use the FX instrument but it will work for backtesting
                instrument = base_fx

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,     # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,      # 95% fill probability on stop orders
            prob_slippage=0.01,          # 1% slippage probability
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

        # Convert database data to Nautilus Bar objects
        # Use the actual instrument.id that matches the instrument we added
        bars = self.data_service.convert_to_nautilus_bars(
            market_data, instrument.id, instrument
        )

        if not bars:
            raise ValueError("No bars were created from the data")

        # Add bars to engine
        self.engine.add_data(bars)

        # Create bar type string for strategy configuration
        # Must match the bar type created in convert_to_nautilus_bars
        # Use the actual instrument.id to ensure consistency
        bar_type_str = f"{instrument.id}-1-MINUTE-MID-EXTERNAL"

        # Configure strategy
        strategy_config = SMAConfig(
            instrument_id=instrument.id,
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

    def run_from_config_file(self, config_file_path: str) -> BacktestResult:
        """
        Run backtest from YAML configuration file.

        Args:
            config_file_path: Path to YAML configuration file

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If configuration is invalid
        """
        # Load configuration from file
        config_obj = ConfigLoader.load_from_file(config_file_path)
        return self.run_from_config_object(config_obj)

    def run_from_config_object(
        self, config_obj: StrategyConfigWrapper
    ) -> BacktestResult:
        """
        Run backtest from loaded configuration object.

        Args:
            config_obj: Loaded strategy configuration wrapper

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If strategy cannot be created or configuration is invalid
        """
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
            prob_fill_on_limit=0.95,     # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,      # 95% fill probability on stop orders
            prob_slippage=0.01,          # 1% slippage probability
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
        bars = generate_mock_bars(instrument_id, num_bars=self.settings.mock_data_bars)

        # Add bars to engine
        self.engine.add_data(bars)

        # Use the StrategyFactory to create the strategy with proper config
        # Create config parameters dictionary with mock data values
        config_params = {
            "instrument_id": instrument_id,
            "bar_type": bar_type_str,
        }

        # Copy specific strategy parameters from the loaded config
        if hasattr(config_obj.config, "fast_period"):
            config_params["fast_period"] = config_obj.config.fast_period
        if hasattr(config_obj.config, "slow_period"):
            config_params["slow_period"] = config_obj.config.slow_period
        if hasattr(config_obj.config, "lookback_period"):
            config_params["lookback_period"] = config_obj.config.lookback_period
        if hasattr(config_obj.config, "num_std_dev"):
            config_params["num_std_dev"] = config_obj.config.num_std_dev
        if hasattr(config_obj.config, "rsi_period"):
            config_params["rsi_period"] = config_obj.config.rsi_period
        if hasattr(config_obj.config, "oversold_threshold"):
            config_params["oversold_threshold"] = config_obj.config.oversold_threshold
        if hasattr(config_obj.config, "overbought_threshold"):
            config_params["overbought_threshold"] = (
                config_obj.config.overbought_threshold
            )
        if hasattr(config_obj.config, "trade_size"):
            config_params["trade_size"] = config_obj.config.trade_size

        # Create strategy using factory method
        strategy = StrategyFactory.create_strategy_from_config(
            strategy_path=config_obj.strategy_path,
            config_path=config_obj.config_path,
            config_params=config_params,
        )
        self.engine.add_strategy(strategy=strategy)

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    def reset(self) -> None:
        """Reset the engine for a new backtest."""
        if self.engine:
            self.engine.reset()
        self._results = None

    async def run_backtest_with_strategy_type(
        self,
        strategy_type: str,
        symbol: str,
        start: datetime,
        end: datetime,
        **strategy_params,
    ) -> BacktestResult:
        """
        Run backtest with specific strategy type using database data.

        Args:
            strategy_type: Type of strategy ("sma_crossover", "mean_reversion", "momentum", or "sma")
            symbol: Trading symbol
            start: Start datetime
            end: End datetime
            **strategy_params: Strategy-specific parameters

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If strategy type is not supported or data is invalid
        """
        from src.models.strategy import StrategyType
        from src.core.strategy_factory import StrategyLoader

        # Handle "sma" alias for backward compatibility
        if strategy_type == "sma":
            strategy_type = "sma_crossover"

        # Validate strategy type
        try:
            strategy_enum = StrategyType(strategy_type)
        except ValueError:
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

        if self.data_source != "database":
            raise ValueError("This method requires 'database' data source")

        if not self.data_service:
            raise ValueError("Data service not initialized")

        # Validate data availability
        validation = await self.data_service.validate_data_availability(
            symbol, start, end
        )
        if not validation["valid"]:
            raise ValueError(f"Data validation failed: {validation['reason']}")

        # Fetch data from database
        market_data = await self.data_service.get_market_data(symbol, start, end)

        if not market_data:
            raise ValueError(
                f"No market data found for {symbol} between {start} and {end}"
            )

        # Create test instrument for the actual symbol with SIM venue
        if "/" in symbol and len(symbol.split("/")) == 2:
            # For FX pairs
            base_instrument, _ = create_test_instrument(symbol)
            instrument = base_instrument
        else:
            # For equity symbols
            clean_symbol = symbol.replace("2018", "18").replace("_", "")[:7]

            from nautilus_trader.test_kit.providers import TestInstrumentProvider

            try:
                instrument = TestInstrumentProvider.equity(
                    symbol=clean_symbol,
                    venue="SIM",
                )
            except Exception:
                # Fallback
                base_fx = TestInstrumentProvider.default_fx_ccy("EUR/USD")
                instrument = base_fx

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,     # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,      # 95% fill probability on stop orders
            prob_slippage=0.01,          # 1% slippage probability
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

        # Convert database data to Nautilus Bar objects
        bars = self.data_service.convert_to_nautilus_bars(
            market_data, instrument.id, instrument
        )

        if not bars:
            raise ValueError("No bars were created from the data")

        # Add bars to engine
        self.engine.add_data(bars)

        # Create bar type string
        bar_type_str = f"{instrument.id}-1-MINUTE-MID-EXTERNAL"

        # Prepare strategy configuration parameters
        from nautilus_trader.model.data import BarType

        config_params = {
            "instrument_id": instrument.id,
            "bar_type": BarType.from_str(bar_type_str),
        }

        # Add strategy-specific parameters based on type
        if strategy_enum == StrategyType.SMA_CROSSOVER:
            config_params.update(
                {
                    "fast_period": strategy_params.get("fast_period", 10),
                    "slow_period": strategy_params.get("slow_period", 20),
                    "trade_size": Decimal(
                        str(strategy_params.get("trade_size", 1000000))
                    ),
                }
            )
        elif strategy_enum == StrategyType.MEAN_REVERSION:
            config_params.update(
                {
                    "trade_size": Decimal(
                        str(strategy_params.get("trade_size", 1000000))
                    ),
                    "order_id_tag": strategy_params.get("order_id_tag", "001"),
                    "rsi_period": strategy_params.get("rsi_period", 2),
                    "rsi_buy_threshold": strategy_params.get("rsi_buy_threshold", 10.0),
                    "exit_rsi": strategy_params.get("exit_rsi", 50.0),
                    "sma_trend_period": strategy_params.get("sma_trend_period", 200),
                    "warmup_days": strategy_params.get("warmup_days", 400),
                    "cooldown_bars": strategy_params.get("cooldown_bars", 0),
                }
            )
        elif strategy_enum == StrategyType.MOMENTUM:
            config_params.update(
                {
                    "trade_size": Decimal(
                        str(strategy_params.get("trade_size", 1000000))
                    ),
                    "order_id_tag": strategy_params.get("order_id_tag", "002"),
                    "fast_period": strategy_params.get("fast_period", 20),
                    "slow_period": strategy_params.get("slow_period", 50),
                    "warmup_days": strategy_params.get("warmup_days", 1),
                    "allow_short": strategy_params.get("allow_short", False),
                }
            )

        # Create strategy using StrategyLoader
        strategy = StrategyLoader.create_strategy(strategy_enum, config_params)
        self.engine.add_strategy(strategy=strategy)

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    def dispose(self) -> None:
        """Dispose of the engine resources."""
        if self.engine:
            self.engine.dispose()
        self._results = None
