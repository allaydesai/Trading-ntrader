"""
Logging Setup Module

This module provides centralized logging configuration for the Nautilus Trading Platform.
It integrates with the configuration system and sets up both console and file logging
with proper rotation and formatting.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional, Dict

from .config_loader import get_config, LoggingConfig


# Global logger registry to track configured loggers
_configured_loggers: Dict[str, logging.Logger] = {}


def setup_logging(config: Optional[LoggingConfig] = None) -> None:
    """
    Configure application-wide logging settings.
    
    This function sets up both console and file logging handlers based on the
    configuration. It should be called once at application startup.
    
    Args:
        config: Optional LoggingConfig instance. If None, loads from global config.
    """
    if config is None:
        app_config = get_config()
        config = app_config.logging
    
    # Ensure log directory exists
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get root logger
    root_logger = logging.getLogger()
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # Set logging level
    log_level = getattr(logging, config.level.upper(), logging.INFO)
    root_logger.setLevel(log_level)
    
    # Create formatter
    formatter = logging.Formatter(
        fmt=config.format,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Set up console handler
    if config.log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # Set up file handler with rotation
    if config.log_to_file:
        log_file_path = log_dir / "trading_platform.log"
        
        try:
            # Convert MB to bytes for maxBytes parameter
            max_bytes = config.max_file_size_mb * 1024 * 1024
            
            file_handler = logging.handlers.RotatingFileHandler(
                filename=str(log_file_path),
                maxBytes=max_bytes,
                backupCount=config.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            
        except (OSError, IOError) as e:
            # If file logging fails, log to console
            console_logger = logging.getLogger(__name__)
            console_logger.error(f"Failed to set up file logging: {e}")
            console_logger.warning("Continuing with console logging only")
    
    # Log initial setup message
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured - Level: {config.level}, "
                f"Console: {config.log_to_console}, File: {config.log_to_file}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.
    
    This function returns a logger that inherits from the root logger configuration.
    It's the preferred way to get loggers throughout the application.
    
    Args:
        name: The name of the logger (typically __name__ of the module).
        
    Returns:
        logging.Logger: Configured logger instance.
    """
    if name not in _configured_loggers:
        logger = logging.getLogger(name)
        _configured_loggers[name] = logger
    
    return _configured_loggers[name]


def configure_module_logger(module_name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Configure a logger for a specific module with optional level override.
    
    This function allows setting different log levels for different modules,
    which is useful for debugging specific components.
    
    Args:
        module_name: Name of the module (e.g., 'src.strategies.base_strategy').
        level: Optional log level override (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        
    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = get_logger(module_name)
    
    if level:
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.setLevel(log_level)
    
    return logger


def setup_component_logging(component_name: str, log_file: Optional[str] = None) -> logging.Logger:
    """
    Set up logging for a specific component with optional separate log file.
    
    This is useful for components that need separate log files (e.g., strategies,
    backtesting, live trading).
    
    Args:
        component_name: Name of the component.
        log_file: Optional separate log file name (without extension).
        
    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = get_logger(component_name)
    
    if log_file:
        try:
            config = get_config().logging
            log_dir = Path(config.log_dir)
            log_file_path = log_dir / f"{log_file}.log"
            
            # Create file handler for this component
            max_bytes = config.max_file_size_mb * 1024 * 1024
            file_handler = logging.handlers.RotatingFileHandler(
                filename=str(log_file_path),
                maxBytes=max_bytes,
                backupCount=config.backup_count,
                encoding='utf-8'
            )
            
            # Use same formatter as main logging
            formatter = logging.Formatter(
                fmt=config.format,
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(formatter)
            
            # Add handler to logger
            logger.addHandler(file_handler)
            
        except (OSError, IOError) as e:
            logger.error(f"Failed to set up component log file {log_file}: {e}")
    
    return logger


def log_system_info() -> None:
    """
    Log system information at startup.
    
    This function logs useful system information that can help with debugging
    and monitoring.
    """
    logger = get_logger(__name__)
    
    try:
        import platform
        import psutil
        
        logger.info("=== System Information ===")
        logger.info(f"Platform: {platform.platform()}")
        logger.info(f"Python version: {platform.python_version()}")
        logger.info(f"CPU cores: {psutil.cpu_count()}")
        logger.info(f"Memory: {psutil.virtual_memory().total / (1024**3):.1f} GB")
        logger.info(f"Available memory: {psutil.virtual_memory().available / (1024**3):.1f} GB")
        logger.info("=== End System Information ===")
        
    except ImportError:
        logger.warning("psutil not available - system information not logged")
    except Exception as e:
        logger.error(f"Error logging system information: {e}")


def shutdown_logging() -> None:
    """
    Shutdown logging gracefully.
    
    This function should be called at application shutdown to ensure
    all log handlers are properly closed.
    """
    logger = get_logger(__name__)
    logger.info("Shutting down logging system")
    
    # Close all handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
    
    # Clear our logger registry
    _configured_loggers.clear()


# Convenience function for quick logger setup
def quick_setup_logging(level: str = "INFO", console_only: bool = False) -> None:
    """
    Quick logging setup for testing or simple scripts.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        console_only: If True, only set up console logging.
    """
    from .config_loader import LoggingConfig
    
    config = LoggingConfig(
        level=level,
        log_to_console=True,
        log_to_file=not console_only
    )
    
    setup_logging(config) 