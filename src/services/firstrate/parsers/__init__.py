"""FirstRate Data parsers — base framework, registry, and asset-specific parsers.

Importing this package auto-registers all bundled parsers so that
``get_parser(AssetClass.ETF)`` and ``get_parser(AssetClass.STOCK)``
work immediately.
"""

from src.services.firstrate.parsers.base import (
    BaseParser,
    RawBarData,
    get_parser,
    register_parser,
)

# Import parser modules to trigger their @register_parser decorators
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser

__all__ = [
    "BaseParser",
    "FirstRateCsvParser",
    "RawBarData",
    "get_parser",
    "register_parser",
]
