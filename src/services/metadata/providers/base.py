"""Provider-agnostic metadata seam (ADR-5).

Defines the ``MetadataProvider`` Protocol that ``InstrumentMetadataService``
depends on, so a second provider swaps in by touching only ``providers/`` plus
the service-construction call site. ``FMPMetadataProvider`` (Story 1.4) already
exposes ``resolve(ticker) -> InstrumentMetadata`` and therefore *structurally*
satisfies this Protocol with no changes — Protocols are duck-typed.
"""

from typing import Protocol

from src.models.instrument_metadata import InstrumentMetadata


class MetadataProvider(Protocol):
    """Resolves a ticker to the domain ``InstrumentMetadata`` (no vendor types out)."""

    def resolve(self, ticker: str) -> InstrumentMetadata:
        """Return domain metadata for ``ticker``; callers isolate provider exceptions."""
        ...
