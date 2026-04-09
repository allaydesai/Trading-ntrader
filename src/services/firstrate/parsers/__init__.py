"""FirstRate Data parsers — base framework, registry, and asset-specific parsers.

Importing this package auto-registers all bundled parsers (ETF, etc.)
so that ``get_parser(AssetClass.ETF)`` works immediately.
"""

from src.services.firstrate.parsers.base import (
    BaseParser,
    RawBarData,
    get_parser,
    register_parser,
)

# Import parser modules to trigger their @register_parser decorators
from src.services.firstrate.parsers.etf_parser import ETFParser

__all__ = [
    "BaseParser",
    "ETFParser",
    "RawBarData",
    "get_parser",
    "register_parser",
]
