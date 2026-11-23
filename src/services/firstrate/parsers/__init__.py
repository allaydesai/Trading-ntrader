"""FirstRate Data parsers — base framework, registry, and asset-specific parsers.

Importing this package auto-registers all bundled parsers so that
``get_parser(AssetClass.ETF)``, ``get_parser(AssetClass.STOCK)``,
``get_parser(AssetClass.FUTURES)`` and ``get_parser(AssetClass.FX)`` work immediately.
"""

from src.services.firstrate.parsers.base import (
    BaseParser,
    RawBarData,
    get_parser,
    register_parser,
)

# Import parser modules to trigger their @register_parser decorators
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser
from src.services.firstrate.parsers.firstrate_futures_parser import FirstRateFuturesParser
from src.services.firstrate.parsers.firstrate_fx_parser import FirstRateFxParser

__all__ = [
    "BaseParser",
    "FirstRateCsvParser",
    "FirstRateFuturesParser",
    "FirstRateFxParser",
    "RawBarData",
    "get_parser",
    "register_parser",
]
