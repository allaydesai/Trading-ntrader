## Relevant Files

- `src/core/` - Directory for core infrastructure components (e.g., Nautilus setup, IB connection, DB interface).
  - `src/core/config_loader.py` - Handles loading application and strategy configurations.
  - `src/core/db_manager.py` - Manages SQLite database connections and operations.
  - `src/core/logging_setup.py` - Configures application-wide logging with console and file handlers, rotation, and component-specific logging.
  - `src/core/ib_connector.py` - Manages connection and interaction with Interactive Brokers API via Nautilus Trader.
- `src/strategies/` - Directory for strategy definitions and management.
  - `src/strategies/base_strategy.py` - Abstract base class for trading strategies.
  - `src/strategies/example_mavg_strategy.py` - An example moving average crossover strategy.
  - `src/strategies/strategy_manager.py` - Handles loading and managing strategy instances.
- `src/backtesting/` - Directory for backtesting engine components.
  - `src/backtesting/engine.py` - Core backtesting logic using Nautilus Trader.
  - `src/backtesting/data_handler.py` - Fetches and prepares historical data for backtests from IB.
  - `src/backtesting/results_analyzer.py` - Calculates and stores backtest performance metrics.
- `src/live_trading/` - Directory for live trading engine components.
  - `src/live_trading/engine.py` - Core live trading logic using Nautilus Trader.
  - `src/live_trading/order_manager.py` - Handles order submission, tracking, and modification with IB.
  - `src/live_trading/position_tracker.py` - Tracks current positions and P&L.
- `src/web_ui/` - Directory for the web-based dashboard (e.g., Flask/Dash app).
  - `src/web_ui/app.py` - Main Flask/Dash application file.
  - `src/web_ui/templates/` - HTML templates for the web UI (if using Flask).
  - `src/web_ui/static/` - Static assets (CSS, JS) for the web UI.
  - `src/web_ui/callbacks.py` - Callbacks for Dash app interactivity.
  - `src/web_ui/views.py` - Views/routes for the Flask app.
- `src/utils/` - Directory for shared utilities.
  - `src/utils/data_models.py` - Pydantic or dataclass models for trades, orders, etc.
  - `src/utils/plotting.py` - Utility functions for generating charts for the dashboard.
- `tests/` - Directory for all unit and integration tests.
  - `tests/core/test_db_manager.py`
  - `tests/strategies/test_example_mavg_strategy.py`
  - `tests/backtesting/test_engine.py`
  - `tests/live_trading/test_order_manager.py`
- `main.py` - Main application entry point (CLI or starting point for services).
- `config/` - Directory for configuration files.
  - `config/app_config.yaml` - Main application configuration.
  - `config/strategy_params/` - Directory for strategy-specific parameter files.
- `data/` - Directory for storing data.
  - `data/trading_platform.db` - SQLite database file.
  - `data/logs/` - Directory for log files.

### Notes

- Unit tests should typically be placed alongside the code files they are testing (e.g., `MyComponent.py` and `MyComponent.test.py` in the same directory or within a parallel `tests` subdirectory structure like `tests/core/test_db_manager.py`).
- Consider using `pytest` for running tests: `pytest [optional/path/to/test/file]`.

## Tasks

- [ ] 1.0 Project Setup and Core Infrastructure
  - [x] 1.1 Initialize project structure (directories like `src`, `tests`, `config`, `data`).
  - [x] 1.2 Set up virtual environment and install base dependencies (Python, Nautilus Trader, Pydantic).
  - [x] 1.3 Implement configuration management (`src/core/config_loader.py`, `config/app_config.yaml`).
  - [x] 1.4 Implement logging setup (`src/core/logging_setup.py`).
  - [ ] 1.5 Develop SQLite database interface (`src/core/db_manager.py`) for storing strategy params, backtest results, trade logs.
    - [x] 1.5.1 Define database schema (tables for strategies, parameters, backtest summaries, trades).
    - [ ] 1.5.2 Implement functions for CRUD operations on the database tables.
  - [ ] 1.6 Implement Interactive Brokers (IB) connection module (`src/core/ib_connector.py`) leveraging Nautilus Trader capabilities.
    - [ ] 1.6.1 Handle IB connection, disconnection, and reconnection logic.
    - [ ] 1.6.2 Ensure IB Gateway/TWS API client is correctly configured and accessible.
  - [ ] 1.7 Define core data models (`src/utils/data_models.py`) for trades, orders, positions, strategy parameters using Pydantic or dataclasses.
  - [ ] 1.8 Write unit tests for core components (config loader, DB manager).

- [ ] 2.0 Strategy Definition and Management Module
  - [ ] 2.1 Design and implement a base strategy class (`src/strategies/base_strategy.py`) within Nautilus Trader framework.
    - [ ] 2.1.1 Define abstract methods for initialization, data handling (on_bar, on_tick - if future), order generation.
    - [ ] 2.1.2 Include support for multi-timeframe data consumption.
    - [ ] 2.1.3 Include interface for consuming external signals (e.g., from ML models).
  - [ ] 2.2 Implement at least one example trading strategy (e.g., Moving Average Crossover - `src/strategies/example_mavg_strategy.py`) inheriting from the base strategy.
  - [ ] 2.3 Develop a strategy manager (`src/strategies/strategy_manager.py`) to load, configure, and manage strategy instances.
    - [ ] 2.3.1 Load strategy parameters from configuration or database.
  - [ ] 2.4 Implement storage and retrieval of strategy parameters using the DB manager.
  - [ ] 2.5 Write unit tests for the example strategy and strategy manager.

- [ ] 3.0 Backtesting Engine Development
  - [ ] 3.1 Implement backtesting data handler (`src/backtesting/data_handler.py`) to fetch historical data (OHLCV, fundamental) from IB via Nautilus Trader.
    - [ ] 3.1.1 Support fetching data for various instruments (stocks, ETFs, Forex) and timeframes.
  - [ ] 3.2 Develop the core backtesting engine (`src/backtesting/engine.py`) using Nautilus Trader.
    - [ ] 3.2.1 Integrate strategy instances with the historical data feed.
    - [ ] 3.2.2 Simulate order execution (consider configurable commission and slippage models from PRD Open Questions).
    - [ ] 3.2.3 Ensure the same strategy class is used as for live trading.
  - [ ] 3.3 Implement backtest results analyzer (`src/backtesting/results_analyzer.py`).
    - [ ] 3.3.1 Calculate key performance metrics (Total Return, Sharpe Ratio, Max Drawdown, etc. as per FR19).
    - [ ] 3.3.2 Generate a list of simulated trades.
  - [ ] 3.4 Store backtest results (summary KPIs, trade list) in the SQLite database.
  - [ ] 3.5 Write unit/integration tests for the backtesting data handler and engine.

- [ ] 4.0 Live Trading Engine Development
  - [ ] 4.1 Implement live trading order manager (`src/live_trading/order_manager.py`) using Nautilus Trader.
    - [ ] 4.1.1 Handle submission, modification, and cancellation of orders (Market, Limit, Stop) to IB.
    - [ ] 4.1.2 Track order statuses (filled, partial fill, cancelled, rejected).
  - [ ] 4.2 Develop a real-time position tracker (`src/live_trading/position_tracker.py`).
    - [ ] 4.2.1 Monitor current open positions and their P&L.
    - [ ] 4.2.2 Synchronize with IB for position updates.
  - [ ] 4.3 Implement the core live trading engine (`src/live_trading/engine.py`) using Nautilus Trader.
    - [ ] 4.3.1 Integrate strategy instances with live market data feeds from IB.
    - [ ] 4.3.2 Connect strategy signals to the order manager for execution.
    - [ ] 4.3.3 Ensure the same strategy class from backtesting is used.
  - [ ] 4.4 Implement logging of all live trading activities (orders, fills, errors) to the database and log files.
  - [ ] 4.5 Write unit/integration tests for the order manager and position tracker.

- [ ] 5.0 Web-Based Performance Dashboard UI
  - [ ] 5.1 Choose and set up a web framework (Flask or Dash - Dash recommended in PRD).
  - [ ] 5.2 Design basic UI layout (`src/web_ui/templates/` or Dash layout definitions in `src/web_ui/app.py`).
  - [ ] 5.3 Implement backend logic to fetch data from the SQLite database for display.
  - [ ] 5.4 Develop UI components to display backtest results:
    - [ ] 5.4.1 Table of key performance metrics (FR19).
    - [ ] 5.4.2 Equity curve chart.
    - [ ] 5.4.3 Drawdown chart.
    - [ ] 5.4.4 List/table of trades.
  - [ ] 5.5 Develop UI components to display live trading information:
    - [ ] 5.5.1 Current open positions and their P&L.
    - [ ] 5.5.2 Account summary (equity, balance).
    - [ ] 5.5.3 List/table of recent live trades and order statuses.
  - [ ] 5.6 Implement basic strategy management controls (e.g., view loaded strategies, potentially start/stop - if feasible for initial version).
  - [ ] 5.7 (Optional) Implement plotting utilities (`src/utils/plotting.py`) if using Flask (Dash has built-in charting).
  - [ ] 5.8 Style the UI for a clean and simple look (basic CSS in `src/web_ui/static/`).
  - [ ] 5.9 Write basic UI interaction tests if possible (e.g., using Selenium for Flask, or specific Dash testing tools).

- [ ] 6.0 System-wide Error Handling and Reliability Features
  - [ ] 6.1 Implement robust error handling for IB API interactions (connection drops, errors in `src/core/ib_connector.py` and live trading modules).
    - [ ] 6.1.1 Implement reconnection logic for IB API.
  - [ ] 6.2 Implement mechanisms to handle market data issues (gaps, errors - within strategy/data handler logic).
  - [ ] 6.3 Implement error handling for order execution (rejections, partial fills in `src/live_trading/order_manager.py`).
    - [ ] 6.3.1 Log errors comprehensively.
    - [ ] 6.3.2 Consider basic alerting or status indicators in the UI for critical errors.
  - [ ] 6.4 Ensure comprehensive logging is implemented across all modules.
  - [ ] 6.5 Conduct integration testing for end-to-end scenarios (e.g., backtest a strategy and view results, run a live strategy with mock IB data if possible).

- [ ] 7.0 Documentation and Finalization
  - [ ] 7.1 Write basic user documentation (README.md) explaining setup, configuration, and how to run the application (backtest, live trade, view dashboard).
  - [ ] 7.2 Document core module APIs and strategy creation process for future development.
  - [ ] 7.3 Perform final code review and refactoring.
  - [ ] 7.4 Ensure all success metrics from PRD section 8 can be verified. 