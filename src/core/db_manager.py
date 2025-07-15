"""
Database Manager Module

This module provides database connectivity and schema management for the Algorithmic Trading Platform.
It defines the database schema and provides the foundation for CRUD operations.
"""

import sqlite3
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import json
import logging

from .config_loader import ConfigLoader


class DatabaseManager:
    """
    Manages SQLite database connections and operations for the trading platform.
    
    This class handles database initialization, schema creation, and provides
    a foundation for database operations throughout the application.
    """
    
    def __init__(self, config_loader: Optional[ConfigLoader] = None):
        """
        Initialize the database manager.
        
        Args:
            config_loader: Optional configuration loader instance
        """
        self.config_loader = config_loader or ConfigLoader()
        self.config = self.config_loader.config
        self.db_path = self.config.database.path
        self.logger = logging.getLogger(__name__)
        
        # Ensure database directory exists
        self._ensure_database_directory()
        
        # Initialize database schema
        self._initialize_database()
    
    def _ensure_database_directory(self) -> None:
        """Ensure the database directory exists."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Database directory ensured: {db_dir}")
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Get a database connection.
        
        Returns:
            sqlite3.Connection: Database connection object
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access to rows
        
        # Enable foreign key constraints
        conn.execute("PRAGMA foreign_keys = ON")
        
        return conn
    
    def _initialize_database(self) -> None:
        """Initialize the database schema by creating all necessary tables."""
        try:
            with self.get_connection() as conn:
                self._create_strategies_table(conn)
                self._create_parameters_table(conn)
                self._create_backtest_summaries_table(conn)
                self._create_trades_table(conn)
                self._create_indexes(conn)
                
                self.logger.info("Database schema initialized successfully")
                
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise
    
    def _create_strategies_table(self, conn: sqlite3.Connection) -> None:
        """
        Create the strategies table for storing strategy metadata and configuration.
        
        This table stores strategy definitions, metadata, and configuration settings.
        """
        sql = """
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            strategy_class TEXT NOT NULL,
            version TEXT DEFAULT '1.0.0',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'archived')),
            timeframe TEXT DEFAULT '1hour' CHECK (timeframe IN ('1min', '5min', '15min', '30min', '1hour', '1day')),
            instruments TEXT, -- JSON array of instrument symbols
            capital_allocation REAL DEFAULT 0.0,
            risk_per_trade REAL DEFAULT 0.02,
            max_positions INTEGER DEFAULT 5,
            config_json TEXT, -- JSON configuration specific to strategy
            notes TEXT
        );
        """
        
        conn.execute(sql)
        self.logger.debug("Created strategies table")
    
    def _create_parameters_table(self, conn: sqlite3.Connection) -> None:
        """
        Create the parameters table for storing strategy-specific parameters.
        
        This table stores configurable parameters for each strategy instance.
        """
        sql = """
        CREATE TABLE IF NOT EXISTS parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            parameter_name TEXT NOT NULL,
            parameter_value TEXT NOT NULL, -- Stored as JSON for flexibility
            parameter_type TEXT DEFAULT 'string' CHECK (parameter_type IN ('string', 'integer', 'float', 'boolean', 'json')),
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE,
            UNIQUE(strategy_id, parameter_name)
        );
        """
        
        conn.execute(sql)
        self.logger.debug("Created parameters table")
    
    def _create_backtest_summaries_table(self, conn: sqlite3.Connection) -> None:
        """
        Create the backtest_summaries table for storing backtest results and performance metrics.
        
        This table stores aggregated backtest results and key performance indicators.
        """
        sql = """
        CREATE TABLE IF NOT EXISTS backtest_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            backtest_name TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            initial_capital REAL NOT NULL,
            final_capital REAL NOT NULL,
            total_return REAL NOT NULL,
            annualized_return REAL,
            sharpe_ratio REAL,
            sortino_ratio REAL,
            max_drawdown REAL,
            max_drawdown_duration INTEGER, -- in days
            win_rate REAL,
            profit_factor REAL,
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            losing_trades INTEGER DEFAULT 0,
            average_win REAL,
            average_loss REAL,
            largest_win REAL,
            largest_loss REAL,
            average_trade_duration REAL, -- in hours
            volatility REAL,
            beta REAL,
            alpha REAL,
            information_ratio REAL,
            calmar_ratio REAL,
            underwater_curve TEXT, -- JSON array of drawdown data
            equity_curve TEXT, -- JSON array of equity progression
            monthly_returns TEXT, -- JSON array of monthly return data
            trade_distribution TEXT, -- JSON histogram of trade returns
            commission_paid REAL DEFAULT 0.0,
            slippage_cost REAL DEFAULT 0.0,
            benchmark_return REAL,
            benchmark_sharpe REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            config_snapshot TEXT, -- JSON snapshot of strategy config at backtest time
            notes TEXT,
            FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE
        );
        """
        
        conn.execute(sql)
        self.logger.debug("Created backtest_summaries table")
    
    def _create_trades_table(self, conn: sqlite3.Connection) -> None:
        """
        Create the trades table for storing individual trade records.
        
        This table stores detailed information about individual trades from both
        backtesting and live trading sessions.
        """
        sql = """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            backtest_id INTEGER, -- NULL for live trades
            trade_type TEXT NOT NULL CHECK (trade_type IN ('backtest', 'live')),
            instrument TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            entry_time TIMESTAMP NOT NULL,
            exit_time TIMESTAMP,
            duration_minutes INTEGER,
            pnl REAL,
            pnl_percent REAL,
            commission REAL DEFAULT 0.0,
            slippage REAL DEFAULT 0.0,
            order_type TEXT DEFAULT 'market' CHECK (order_type IN ('market', 'limit', 'stop', 'stop_limit')),
            entry_reason TEXT, -- Signal or condition that triggered entry
            exit_reason TEXT, -- Signal or condition that triggered exit
            status TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
            
            -- Live trading specific fields
            order_id TEXT, -- Broker order ID for live trades
            account_id TEXT, -- Account ID for live trades
            
            -- Risk management fields
            stop_loss_price REAL,
            take_profit_price REAL,
            position_size_percent REAL,
            portfolio_value_at_entry REAL,
            
            -- Market data at trade time
            bid_price REAL,
            ask_price REAL,
            spread REAL,
            volume REAL,
            
            -- Additional metadata
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT, -- Additional trade-specific data as JSON
            
            FOREIGN KEY (strategy_id) REFERENCES strategies (id) ON DELETE CASCADE,
            FOREIGN KEY (backtest_id) REFERENCES backtest_summaries (id) ON DELETE SET NULL
        );
        """
        
        conn.execute(sql)
        self.logger.debug("Created trades table")
    
    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        """
        Create database indexes for performance optimization.
        
        These indexes improve query performance on frequently accessed columns.
        """
        indexes = [
            # Strategies table indexes
            "CREATE INDEX IF NOT EXISTS idx_strategies_name ON strategies(name)",
            "CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status)",
            "CREATE INDEX IF NOT EXISTS idx_strategies_timeframe ON strategies(timeframe)",
            
            # Parameters table indexes
            "CREATE INDEX IF NOT EXISTS idx_parameters_strategy_id ON parameters(strategy_id)",
            "CREATE INDEX IF NOT EXISTS idx_parameters_name ON parameters(parameter_name)",
            
            # Backtest summaries table indexes
            "CREATE INDEX IF NOT EXISTS idx_backtest_summaries_strategy_id ON backtest_summaries(strategy_id)",
            "CREATE INDEX IF NOT EXISTS idx_backtest_summaries_dates ON backtest_summaries(start_date, end_date)",
            "CREATE INDEX IF NOT EXISTS idx_backtest_summaries_return ON backtest_summaries(total_return)",
            "CREATE INDEX IF NOT EXISTS idx_backtest_summaries_sharpe ON backtest_summaries(sharpe_ratio)",
            
            # Trades table indexes
            "CREATE INDEX IF NOT EXISTS idx_trades_strategy_id ON trades(strategy_id)",
            "CREATE INDEX IF NOT EXISTS idx_trades_backtest_id ON trades(backtest_id)",
            "CREATE INDEX IF NOT EXISTS idx_trades_type ON trades(trade_type)",
            "CREATE INDEX IF NOT EXISTS idx_trades_instrument ON trades(instrument)",
            "CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)",
            "CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time)",
            "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)",
            "CREATE INDEX IF NOT EXISTS idx_trades_pnl ON trades(pnl)",
            "CREATE INDEX IF NOT EXISTS idx_trades_order_id ON trades(order_id)",
            
            # Composite indexes for common queries
            "CREATE INDEX IF NOT EXISTS idx_trades_strategy_instrument ON trades(strategy_id, instrument)",
            "CREATE INDEX IF NOT EXISTS idx_trades_strategy_time ON trades(strategy_id, entry_time)",
            "CREATE INDEX IF NOT EXISTS idx_trades_backtest_time ON trades(backtest_id, entry_time)",
        ]
        
        for index_sql in indexes:
            conn.execute(index_sql)
        
        self.logger.debug("Created database indexes")
    
    def get_database_info(self) -> Dict[str, Any]:
        """
        Get information about the database and its tables.
        
        Returns:
            Dict containing database metadata and table information
        """
        info = {
            'database_path': self.db_path,
            'database_exists': os.path.exists(self.db_path),
            'tables': {}
        }
        
        try:
            with self.get_connection() as conn:
                # Get table information
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [row[0] for row in cursor.fetchall()]
                
                for table_name in table_names:
                    # Get table schema
                    cursor = conn.execute(f"PRAGMA table_info({table_name})")
                    columns = cursor.fetchall()
                    
                    # Get row count
                    cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                    row_count = cursor.fetchone()[0]
                    
                    info['tables'][table_name] = {
                        'columns': [{'name': col[1], 'type': col[2], 'notnull': col[3], 'default': col[4], 'pk': col[5]} for col in columns],
                        'row_count': row_count
                    }
                    
        except Exception as e:
            self.logger.error(f"Failed to get database info: {e}")
            info['error'] = str(e)
        
        return info
    
    def validate_schema(self) -> bool:
        """
        Validate that the database schema is properly created.
        
        Returns:
            bool: True if schema is valid, False otherwise
        """
        required_tables = ['strategies', 'parameters', 'backtest_summaries', 'trades']
        
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                existing_tables = [row[0] for row in cursor.fetchall()]
                
                missing_tables = [table for table in required_tables if table not in existing_tables]
                
                if missing_tables:
                    self.logger.error(f"Missing tables: {missing_tables}")
                    return False
                
                # Validate foreign key constraints are enabled
                cursor = conn.execute("PRAGMA foreign_keys")
                fk_enabled = cursor.fetchone()[0]
                
                if not fk_enabled:
                    self.logger.error("Foreign key constraints are not enabled")
                    return False
                
                self.logger.info("Database schema validation successful")
                return True
                
        except Exception as e:
            self.logger.error(f"Schema validation failed: {e}")
            return False
    
    def reset_database(self) -> None:
        """
        Reset the database by dropping all tables and recreating the schema.
        
        WARNING: This will delete all data in the database!
        """
        self.logger.warning("Resetting database - all data will be lost!")
        
        try:
            with self.get_connection() as conn:
                # Drop all tables
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [row[0] for row in cursor.fetchall()]
                
                for table_name in table_names:
                    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                
                # Recreate schema
                self._initialize_database()
                
                self.logger.info("Database reset completed successfully")
                
        except Exception as e:
            self.logger.error(f"Failed to reset database: {e}")
            raise
    
    def backup_database(self, backup_path: Optional[str] = None) -> str:
        """
        Create a backup of the database.
        
        Args:
            backup_path: Optional path for backup file
            
        Returns:
            str: Path to the backup file
        """
        if backup_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f"{self.db_path}.backup_{timestamp}"
        
        try:
            with self.get_connection() as conn:
                backup_conn = sqlite3.connect(backup_path)
                conn.backup(backup_conn)
                backup_conn.close()
            
            self.logger.info(f"Database backup created: {backup_path}")
            return backup_path
            
        except Exception as e:
            self.logger.error(f"Failed to create database backup: {e}")
            raise
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        # No cleanup needed for this implementation
        pass 