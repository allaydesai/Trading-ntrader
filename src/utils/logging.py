"""
Structured logging configuration for NTrader.

This module configures structlog to output logs to both console and file.
Console output is formatted for readability, while file output is JSON formatted
for machine parsing.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Dict

import structlog
from structlog.types import Processor

from src.config import get_settings


def configure_logging() -> None:
    """
    Configure structured logging for the application.
    
    Sets up:
    1. Console handler with colored output (INFO level by default)
    2. File handler with JSON output (DEBUG level by default)
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Log file path
    log_file = log_dir / "ntrader.log"

    # Shared processors for both console and file
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure standard library logging
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear existing handlers

    # 1. Console Handler (Human readable)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # Use ConsoleRenderer for colorful output in development
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 2. File Handler (JSON formatted for parsing)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file for troubleshooting
    
    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Configure structlog to use standard library logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Log startup message
    logger = structlog.get_logger()
    logger.info("logging_configured", log_level=settings.log_level, log_file=str(log_file))

