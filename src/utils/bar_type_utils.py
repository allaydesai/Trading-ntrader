"""Utility functions for bar type parsing and manipulation."""


def parse_bar_type_spec(bar_type_str: str) -> str:
    """
    Extract bar type specification from full bar type string.

    Parses bar type strings in various formats to extract the core
    specification (e.g., "1-DAY-LAST").

    Args:
        bar_type_str: Full bar type string, e.g., "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
                     or "AAPL.NASDAQ-1-HOUR-MID"

    Returns:
        Simplified bar type spec, e.g., "1-DAY-LAST" or "1-HOUR-MID"
        Falls back to "1-DAY-LAST" if parsing fails.

    Example:
        >>> parse_bar_type_spec("AMD.NASDAQ-1-DAY-LAST-EXTERNAL")
        "1-DAY-LAST"
        >>> parse_bar_type_spec("AAPL.NASDAQ-1-HOUR-MID")
        "1-HOUR-MID"
        >>> parse_bar_type_spec("invalid")
        "1-DAY-LAST"
    """
    if not bar_type_str:
        return "1-DAY-LAST"

    parts = bar_type_str.split("-")

    # Expected format: SYMBOL.VENUE-STEP-STEP_TYPE-PRICE_TYPE[-AGG_SOURCE]
    # Parts after split: [SYMBOL.VENUE, STEP, STEP_TYPE, PRICE_TYPE, AGG_SOURCE?]
    if len(parts) >= 4:
        # Extract STEP-STEP_TYPE-PRICE_TYPE (indices 1, 2, 3)
        return f"{parts[1]}-{parts[2]}-{parts[3]}"

    return "1-DAY-LAST"
