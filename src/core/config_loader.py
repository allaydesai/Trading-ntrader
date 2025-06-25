"""
Configuration Loader Module

This module provides functionality to load and validate application and strategy configurations.
It uses Pydantic models for type validation and provides a centralized way to access
configuration settings throughout the application.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings


class AppConfig(BaseModel):
    """Application metadata configuration."""
    name: str = "Nautilus Trading Platform"
    version: str = "1.0.0"
    environment: str = Field(default="development", pattern="^(development|testing|production)$")
    debug: bool = True


class DatabaseConfig(BaseModel):
    """Database configuration settings."""
    path: str = "data/trading_platform.db"
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20

    @validator('path')
    def validate_path(cls, v):
        """Ensure the database directory exists."""
        db_path = Path(v)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return v


class LoggingConfig(BaseModel):
    """Logging configuration settings."""
    level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_dir: str = "data/logs"
    max_file_size_mb: int = 10
    backup_count: int = 5
    log_to_console: bool = True
    log_to_file: bool = True

    @validator('log_dir')
    def validate_log_dir(cls, v):
        """Ensure the log directory exists."""
        Path(v).mkdir(parents=True, exist_ok=True)
        return v


class InteractiveBrokersConfig(BaseModel):
    """Interactive Brokers connection configuration."""
    host: str = "127.0.0.1"
    port: int = Field(default=7497, ge=1, le=65535)
    client_id: int = Field(default=1, ge=1, le=32)
    account_id: str = ""
    connection_timeout: int = Field(default=30, ge=1, le=300)
    request_timeout: int = Field(default=10, ge=1, le=60)
    max_requests_per_second: int = Field(default=45, ge=1, le=50)
    paper_trading: bool = True


class MarketDataConfig(BaseModel):
    """Market data configuration settings."""
    primary_source: str = "interactive_brokers"
    subscription_timeout: int = Field(default=30, ge=1, le=300)
    heartbeat_interval: int = Field(default=30, ge=1, le=300)
    max_bars_per_request: int = Field(default=5000, ge=1, le=50000)
    supported_timeframes: List[str] = ["1min", "5min", "15min", "30min", "1hour", "1day"]


class StrategiesConfig(BaseModel):
    """Strategy configuration settings."""
    base_capital: float = Field(default=100000.0, gt=0)
    max_portfolio_risk: float = Field(default=0.02, gt=0, le=1.0)
    max_positions: int = Field(default=10, ge=1, le=100)
    default_timeframe: str = "1hour"

    @validator('default_timeframe')
    def validate_timeframe(cls, v):
        """Ensure the default timeframe is supported."""
        supported = ["1min", "5min", "15min", "30min", "1hour", "1day"]
        if v not in supported:
            raise ValueError(f"Timeframe {v} not in supported timeframes: {supported}")
        return v


class BacktestingConfig(BaseModel):
    """Backtesting configuration settings."""
    initial_capital: float = Field(default=100000.0, gt=0)
    commission_rate: float = Field(default=0.005, ge=0, le=0.1)
    slippage_bps: int = Field(default=1, ge=0, le=100)
    start_date: str = "2020-01-01"
    end_date: str = "2023-12-31"
    benchmark: str = "SPY"

    @validator('start_date', 'end_date')
    def validate_date_format(cls, v):
        """Validate date format."""
        from datetime import datetime
        try:
            datetime.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Date {v} must be in YYYY-MM-DD format")
        return v


class LiveTradingConfig(BaseModel):
    """Live trading configuration settings."""
    auto_start: bool = False
    position_size_method: str = Field(default="percent_risk", pattern="^(fixed|percent_risk|kelly)$")
    default_position_size: float = Field(default=0.01, gt=0, le=1.0)
    max_daily_loss: float = Field(default=0.05, gt=0, le=1.0)
    max_weekly_loss: float = Field(default=0.10, gt=0, le=1.0)
    order_timeout: int = Field(default=300, ge=1, le=3600)


class WebUIConfig(BaseModel):
    """Web UI configuration settings."""
    host: str = "127.0.0.1"
    port: int = Field(default=8050, ge=1024, le=65535)
    debug: bool = True
    auto_reload: bool = True
    theme: str = Field(default="light", pattern="^(light|dark)$")
    refresh_interval: int = Field(default=5, ge=1, le=60)


class RiskManagementConfig(BaseModel):
    """Risk management configuration settings."""
    max_leverage: float = Field(default=1.0, ge=1.0, le=10.0)
    stop_loss_percent: float = Field(default=0.02, gt=0, le=1.0)
    take_profit_percent: float = Field(default=0.04, gt=0, le=2.0)
    position_timeout_hours: int = Field(default=72, ge=1, le=720)


class NotificationConfig(BaseModel):
    """Notification configuration settings."""
    enabled: bool = False
    email: Dict[str, Any] = {}


class PerformanceConfig(BaseModel):
    """Performance metrics configuration."""
    benchmark_symbols: List[str] = ["SPY", "QQQ", "TLT"]
    risk_free_rate: float = Field(default=0.02, ge=0, le=0.2)
    calculate_rolling_metrics: bool = True
    rolling_window_days: int = Field(default=252, ge=30, le=1000)


class TradingPlatformConfig(BaseModel):
    """Main configuration model that combines all configuration sections."""
    app: AppConfig
    database: DatabaseConfig
    logging: LoggingConfig
    interactive_brokers: InteractiveBrokersConfig
    market_data: MarketDataConfig
    strategies: StrategiesConfig
    backtesting: BacktestingConfig
    live_trading: LiveTradingConfig
    web_ui: WebUIConfig
    risk_management: RiskManagementConfig
    notifications: NotificationConfig
    performance: PerformanceConfig


class ConfigLoader:
    """
    Configuration loader that handles loading and validating configuration files.
    
    This class provides methods to load configurations from YAML files and 
    environment variables, with validation using Pydantic models.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the ConfigLoader.
        
        Args:
            config_path: Path to the configuration file. If None, uses default path.
        """
        self.config_path = config_path or "config/app_config.yaml"
        self._config: Optional[TradingPlatformConfig] = None
        
    def load_config(self, reload: bool = False) -> TradingPlatformConfig:
        """
        Load and validate the configuration.
        
        Args:
            reload: If True, reload the configuration even if already loaded.
            
        Returns:
            TradingPlatformConfig: The validated configuration object.
            
        Raises:
            FileNotFoundError: If the configuration file is not found.
            ValueError: If the configuration is invalid.
        """
        if self._config is not None and not reload:
            return self._config
            
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
            
        try:
            with open(self.config_path, 'r', encoding='utf-8') as file:
                config_data = yaml.safe_load(file)
                
            # Override with environment variables if they exist
            config_data = self._apply_env_overrides(config_data)
            
            # Validate and create configuration object
            self._config = TradingPlatformConfig(**config_data)
            
            return self._config
            
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML configuration: {e}")
        except Exception as e:
            raise ValueError(f"Error loading configuration: {e}")
    
    def _apply_env_overrides(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply environment variable overrides to configuration data.
        
        Environment variables should follow the pattern: TRADING_PLATFORM_<SECTION>_<KEY>
        For example: TRADING_PLATFORM_DATABASE_PATH
        
        Args:
            config_data: The configuration data from YAML file.
            
        Returns:
            Dict[str, Any]: Configuration data with environment overrides applied.
        """
        env_prefix = "TRADING_PLATFORM_"
        
        for env_var, value in os.environ.items():
            if not env_var.startswith(env_prefix):
                continue
                
            # Remove prefix and split into section and key
            var_parts = env_var[len(env_prefix):].lower().split('_', 1)
            if len(var_parts) != 2:
                continue
                
            section, key = var_parts
            
            if section in config_data and key in config_data[section]:
                # Convert string values to appropriate types
                config_data[section][key] = self._convert_env_value(value)
                
        return config_data
    
    def _convert_env_value(self, value: str) -> Union[str, int, float, bool]:
        """
        Convert environment variable string value to appropriate Python type.
        
        Args:
            value: The environment variable value as string.
            
        Returns:
            Union[str, int, float, bool]: The converted value.
        """
        # Handle boolean values
        if value.lower() in ('true', 'yes', '1', 'on'):
            return True
        elif value.lower() in ('false', 'no', '0', 'off'):
            return False
        
        # Handle numeric values
        try:
            if '.' in value:
                return float(value)
            else:
                return int(value)
        except ValueError:
            pass
            
        # Return as string if no conversion possible
        return value
    
    def get_strategy_config(self, strategy_name: str) -> Dict[str, Any]:
        """
        Load strategy-specific configuration from strategy_params directory.
        
        Args:
            strategy_name: Name of the strategy to load configuration for.
            
        Returns:
            Dict[str, Any]: Strategy-specific configuration.
            
        Raises:
            FileNotFoundError: If strategy configuration file is not found.
        """
        strategy_config_path = f"config/strategy_params/{strategy_name}.yaml"
        
        if not os.path.exists(strategy_config_path):
            raise FileNotFoundError(f"Strategy configuration not found: {strategy_config_path}")
            
        try:
            with open(strategy_config_path, 'r', encoding='utf-8') as file:
                return yaml.safe_load(file)
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing strategy configuration: {e}")
    
    def save_strategy_config(self, strategy_name: str, config: Dict[str, Any]) -> None:
        """
        Save strategy-specific configuration to strategy_params directory.
        
        Args:
            strategy_name: Name of the strategy.
            config: Strategy configuration to save.
        """
        strategy_config_path = f"config/strategy_params/{strategy_name}.yaml"
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(strategy_config_path), exist_ok=True)
        
        try:
            with open(strategy_config_path, 'w', encoding='utf-8') as file:
                yaml.dump(config, file, default_flow_style=False, indent=2)
        except Exception as e:
            raise ValueError(f"Error saving strategy configuration: {e}")
    
    @property
    def config(self) -> TradingPlatformConfig:
        """Get the current configuration, loading it if not already loaded."""
        if self._config is None:
            return self.load_config()
        return self._config


# Global configuration instance
_config_loader = ConfigLoader()

def get_config(reload: bool = False) -> TradingPlatformConfig:
    """
    Get the global configuration instance.
    
    Args:
        reload: If True, reload the configuration.
        
    Returns:
        TradingPlatformConfig: The global configuration object.
    """
    return _config_loader.load_config(reload=reload)


def get_strategy_config(strategy_name: str) -> Dict[str, Any]:
    """
    Get strategy-specific configuration.
    
    Args:
        strategy_name: Name of the strategy.
        
    Returns:
        Dict[str, Any]: Strategy configuration.
    """
    return _config_loader.get_strategy_config(strategy_name)


def save_strategy_config(strategy_name: str, config: Dict[str, Any]) -> None:
    """
    Save strategy-specific configuration.
    
    Args:
        strategy_name: Name of the strategy.
        config: Strategy configuration to save.
    """
    _config_loader.save_strategy_config(strategy_name, config) 