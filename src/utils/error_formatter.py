"""Rich console error formatter for Trading-NTrader system.

This module provides formatted error output using Rich console with
colors, icons, and structured layouts for better user experience.

Features:
- Color-coded severity levels
- Icon prefixes for visual identification
- Formatted resolution steps with numbering
- Technical details in expandable sections
- Exit code mapping for automation
"""

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.utils.error_messages import (
    ErrorCategory,
    ErrorMessage,
    ErrorSeverity,
)

# Icon mapping for different severity levels
SEVERITY_ICONS = {
    ErrorSeverity.INFO: "ℹ️",
    ErrorSeverity.WARNING: "⚠️",
    ErrorSeverity.ERROR: "❌",
    ErrorSeverity.CRITICAL: "🔥",
}

# Color mapping for severity levels
SEVERITY_COLORS = {
    ErrorSeverity.INFO: "cyan",
    ErrorSeverity.WARNING: "yellow",
    ErrorSeverity.ERROR: "red",
    ErrorSeverity.CRITICAL: "bold red",
}

# Category icons for visual grouping
CATEGORY_ICONS = {
    ErrorCategory.DATA: "📊",
    ErrorCategory.CONNECTION: "🔌",
    ErrorCategory.INPUT: "⌨️",
    ErrorCategory.SYSTEM: "🖥️",
    ErrorCategory.RATE_LIMIT: "⏱️",
    ErrorCategory.CORRUPTION: "💥",
}

# Exit codes mapping
EXIT_CODE_MAP = {
    ErrorCategory.DATA: 1,
    ErrorCategory.INPUT: 2,
    ErrorCategory.CONNECTION: 3,
    ErrorCategory.SYSTEM: 4,
    ErrorCategory.RATE_LIMIT: 3,  # Treat as connection issue
    ErrorCategory.CORRUPTION: 4,  # Treat as system issue
}


class ErrorFormatter:
    """Format and display error messages using Rich console.

    Provides structured, color-coded error output with actionable
    resolution steps and technical details.

    Attributes:
        console: Rich Console instance for formatted output
    """

    def __init__(self, console: Optional[Console] = None):
        """Initialize error formatter.

        Args:
            console: Optional Rich Console instance. Creates new if None.
        """
        self.console = console or Console()

    def format_error(self, error: ErrorMessage, show_technical: bool = True) -> None:
        """Format and display error message with Rich formatting.

        Args:
            error: ErrorMessage to format and display
            show_technical: Whether to show technical details section

        Example:
            >>> formatter = ErrorFormatter()
            >>> formatter.format_error(DATA_NOT_FOUND_NO_IBKR)
        """
        # Build title with severity icon and color
        icon = SEVERITY_ICONS.get(error.severity, "•")
        category_icon = CATEGORY_ICONS.get(error.category, "")
        severity_color = SEVERITY_COLORS.get(error.severity, "white")

        title_text = Text()
        title_text.append(f"{icon} ", style=severity_color)
        title_text.append(f"{category_icon} ", style="bold")
        title_text.append(error.title, style=f"bold {severity_color}")

        # Build message content
        content_parts = []

        # Main error message
        content_parts.append(Text(error.message, style="white"))
        content_parts.append(Text())  # Empty line

        # Resolution steps section
        if error.resolution_steps:
            content_parts.append(Text("💡 Resolution Steps:", style="bold cyan"))
            for idx, step in enumerate(error.resolution_steps, 1):
                step_text = Text()
                step_text.append(f"  {idx}. ", style="cyan")
                step_text.append(step, style="white")
                content_parts.append(step_text)
            content_parts.append(Text())  # Empty line

        # Technical details section (collapsible)
        if show_technical and error.technical_details:
            content_parts.append(Text("🔧 Technical Details:", style="bold dim"))
            content_parts.append(Text(f"  {error.technical_details}", style="dim"))

        # Combine all parts
        full_content = Text()
        for idx, part in enumerate(content_parts):
            if idx > 0:
                full_content.append("\n")
            full_content.append(part)

        # Create panel with border color based on severity
        panel = Panel(
            full_content,
            title=title_text,
            border_style=severity_color,
            padding=(1, 2),
        )

        # Print to console
        self.console.print(panel)

    def format_error_summary(
        self, errors: list[ErrorMessage], title: str = "Error Summary"
    ) -> None:
        """Format and display multiple errors in a summary table.

        Args:
            errors: List of ErrorMessage objects to summarize
            title: Title for the summary table

        Example:
            >>> formatter = ErrorFormatter()
            >>> formatter.format_error_summary([error1, error2])
        """
        table = Table(
            title=title,
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )

        table.add_column("Severity", style="bold")
        table.add_column("Category", style="bold")
        table.add_column("Title")
        table.add_column("Exit Code", justify="right")

        for error in errors:
            severity_icon = SEVERITY_ICONS.get(error.severity, "•")
            category_icon = CATEGORY_ICONS.get(error.category, "")
            severity_color = SEVERITY_COLORS.get(error.severity, "white")
            exit_code = EXIT_CODE_MAP.get(error.category, 1)

            table.add_row(
                f"{severity_icon} {error.severity.value.upper()}",
                f"{category_icon} {error.category.value.upper()}",
                error.title,
                str(exit_code),
                style=severity_color,
            )

        self.console.print(table)

    def print_success(self, message: str, details: Optional[str] = None) -> None:
        """Print success message with checkmark icon.

        Args:
            message: Success message to display
            details: Optional additional details

        Example:
            >>> formatter = ErrorFormatter()
            >>> formatter.print_success("Data loaded successfully")
        """
        content = Text()
        content.append("✓ ", style="bold green")
        content.append(message, style="green")

        if details:
            content.append("\n")
            content.append(f"  {details}", style="dim green")

        self.console.print(content)

    def print_warning(self, message: str, details: Optional[str] = None) -> None:
        """Print warning message with warning icon.

        Args:
            message: Warning message to display
            details: Optional additional details

        Example:
            >>> formatter = ErrorFormatter()
            >>> formatter.print_warning("Partial data available")
        """
        content = Text()
        content.append("⚠ ", style="bold yellow")
        content.append(message, style="yellow")

        if details:
            content.append("\n")
            content.append(f"  {details}", style="dim yellow")

        self.console.print(content)

    def print_info(self, message: str, details: Optional[str] = None) -> None:
        """Print informational message with info icon.

        Args:
            message: Information message to display
            details: Optional additional details

        Example:
            >>> formatter = ErrorFormatter()
            >>> formatter.print_info("Checking data availability...")
        """
        content = Text()
        content.append("ℹ ", style="bold cyan")
        content.append(message, style="cyan")

        if details:
            content.append("\n")
            content.append(f"  {details}", style="dim cyan")

        self.console.print(content)

    def get_exit_code(self, error: ErrorMessage) -> int:
        """Get exit code for given error message.

        Args:
            error: ErrorMessage to get exit code for

        Returns:
            Exit code (0=success, 1=data, 2=input, 3=connection, 4=system)

        Example:
            >>> formatter = ErrorFormatter()
            >>> code = formatter.get_exit_code(DATA_NOT_FOUND_NO_IBKR)
            >>> print(code)  # Output: 1
        """
        return EXIT_CODE_MAP.get(error.category, 1)


def format_exception_for_display(exception: Exception, show_traceback: bool = False) -> str:
    """Format exception for user-friendly display.

    Args:
        exception: Exception to format
        show_traceback: Whether to include full traceback

    Returns:
        Formatted exception string

    Example:
        >>> try:
        ...     raise ValueError("Invalid input")
        ... except ValueError as e:
        ...     print(format_exception_for_display(e))
    """
    if show_traceback:
        import traceback

        return "".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        )

    return f"{type(exception).__name__}: {str(exception)}"


# Global formatter instance for convenience
_default_formatter: Optional[ErrorFormatter] = None


def get_default_formatter() -> ErrorFormatter:
    """Get or create default error formatter instance.

    Returns:
        Global ErrorFormatter instance

    Example:
        >>> formatter = get_default_formatter()
        >>> formatter.print_success("Operation completed")
    """
    global _default_formatter
    if _default_formatter is None:
        _default_formatter = ErrorFormatter()
    return _default_formatter
