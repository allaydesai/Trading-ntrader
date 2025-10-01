"""Text report generation service using Rich formatting."""

from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime
import pandas as pd

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.align import Align


class TextReportGenerator:
    """Generate formatted text reports using Rich library."""

    def __init__(self):
        """Initialize the text report generator."""
        self.console = Console(width=120, legacy_windows=False)

    def generate_performance_report(self, metrics: Dict[str, Any]) -> str:
        """
        Generate comprehensive performance report.

        Args:
            metrics: Performance metrics dictionary

        Returns:
            Formatted report string
        """
        with self.console.capture() as capture:
            self._render_summary_panel(metrics)
            self.console.print()  # Add spacing
            self._render_returns_table(metrics)
            self.console.print()  # Add spacing
            self._render_risk_table(metrics)
            self.console.print()  # Add spacing
            self._render_trading_table(metrics)

        return capture.get()

    def generate_trade_history_report(self, trades: List[Dict[str, Any]]) -> str:
        """
        Generate trade history report.

        Args:
            trades: List of trade dictionaries

        Returns:
            Formatted trade history string
        """
        with self.console.capture() as capture:
            self._render_trade_history_table(trades)

        return capture.get()

    def generate_equity_curve_report(self, equity_curve: pd.Series) -> str:
        """
        Generate text representation of equity curve.

        Args:
            equity_curve: Time-indexed equity values

        Returns:
            Formatted equity curve string
        """
        with self.console.capture() as capture:
            self._render_equity_curve_summary(equity_curve)

        return capture.get()

    def generate_comprehensive_report(
        self,
        metrics: Dict[str, Any],
        trades: List[Dict[str, Any]],
        equity_curve: pd.Series
    ) -> str:
        """
        Generate comprehensive report with all sections.

        Args:
            metrics: Performance metrics
            trades: Trade history
            equity_curve: Equity curve data

        Returns:
            Complete formatted report
        """
        with self.console.capture() as capture:
            # Header
            title = Text("📊 Comprehensive Backtest Report", style="bold blue")
            self.console.print(Align.center(title))
            self.console.print()

            # Performance summary
            self._render_summary_panel(metrics)
            self.console.print()

            # Detailed metrics
            self._render_returns_table(metrics)
            self.console.print()
            self._render_risk_table(metrics)
            self.console.print()
            self._render_trading_table(metrics)
            self.console.print()

            # Equity curve summary
            self._render_equity_curve_summary(equity_curve)
            self.console.print()

            # Trade history (limited to recent trades to avoid massive output)
            recent_trades = trades[-20:] if len(trades) > 20 else trades
            self._render_trade_history_table(recent_trades)

            if len(trades) > 20:
                self.console.print(f"\n[italic]Showing most recent 20 trades out of {len(trades)} total[/italic]")

        return capture.get()

    def generate_strategy_attribution_report(self, strategy_performance: Dict[str, Dict]) -> str:
        """
        Generate strategy performance attribution report.

        Args:
            strategy_performance: Performance by strategy

        Returns:
            Formatted strategy attribution report
        """
        with self.console.capture() as capture:
            self._render_strategy_attribution_table(strategy_performance)

        return capture.get()

    def export_performance_report(
        self,
        metrics: Dict[str, Any],
        output_path: str,
        trades: Optional[List[Dict[str, Any]]] = None,
        equity_curve: Optional[pd.Series] = None
    ) -> bool:
        """
        Export performance report to file.

        Args:
            metrics: Performance metrics
            output_path: File path for export
            trades: Optional trade history
            equity_curve: Optional equity curve data

        Returns:
            True if export successful, False otherwise
        """
        try:
            if trades is not None and equity_curve is not None:
                content = self.generate_comprehensive_report(metrics, trades, equity_curve)
            else:
                content = self.generate_performance_report(metrics)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)

            return True
        except Exception as e:
            self.console.print(f"[red]Error exporting report: {e}[/red]")
            return False

    def _render_summary_panel(self, metrics: Dict[str, Any]) -> None:
        """Render performance summary panel."""
        if not metrics:
            summary_text = "No performance data available"
        else:
            total_return = self._format_percentage(metrics.get('total_return', 0))
            sharpe_ratio = self._format_number(metrics.get('sharpe_ratio', 0))
            max_drawdown = self._format_percentage(metrics.get('max_drawdown', 0))
            win_rate = self._format_percentage(metrics.get('win_rate', 0))

            summary_text = f"""
        Total Return: {total_return}
        Sharpe Ratio: {sharpe_ratio}
        Max Drawdown: {max_drawdown}
        Win Rate: {win_rate}
        """

        panel = Panel(
            summary_text.strip(),
            title="📊 Performance Summary",
            border_style="blue",
            padding=(1, 2)
        )
        self.console.print(panel)

    def _render_returns_table(self, metrics: Dict[str, Any]) -> None:
        """Render returns analysis table."""
        table = Table(title="📈 Returns Analysis", style="green")
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        returns_metrics = [
            ("Total Return", self._format_percentage(metrics.get('total_return', 0))),
            ("CAGR", self._format_percentage(metrics.get('cagr', 0))),
            ("Annual Return", self._format_percentage(metrics.get('annualized_return', 0))),
            ("Volatility", self._format_percentage(metrics.get('volatility', 0))),
        ]

        for metric, value in returns_metrics:
            table.add_row(metric, value)

        self.console.print(table)

    def _render_risk_table(self, metrics: Dict[str, Any]) -> None:
        """Render risk metrics table."""
        table = Table(title="⚠️ Risk Metrics", style="yellow")
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="yellow")

        risk_metrics = [
            ("Sharpe Ratio", self._format_number(metrics.get('sharpe_ratio', 0))),
            ("Sortino Ratio", self._format_number(metrics.get('sortino_ratio', 0))),
            ("Max Drawdown", self._format_percentage(metrics.get('max_drawdown', 0))),
            ("Calmar Ratio", self._format_number(metrics.get('calmar_ratio', 0))),
        ]

        # Add drawdown recovery info if available
        if metrics.get('max_drawdown_date'):
            risk_metrics.append(("Max DD Date", self._format_date(metrics.get('max_drawdown_date'))))

        if metrics.get('recovery_days'):
            risk_metrics.append(("Recovery Days", str(metrics.get('recovery_days'))))

        for metric, value in risk_metrics:
            table.add_row(metric, value)

        self.console.print(table)

    def _render_trading_table(self, metrics: Dict[str, Any]) -> None:
        """Render trading statistics table."""
        table = Table(title="📊 Trading Statistics", style="magenta")
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="magenta")

        trading_metrics = [
            ("Total Trades", str(metrics.get('total_trades', 0))),
            ("Winning Trades", str(metrics.get('winning_trades', 0))),
            ("Losing Trades", str(metrics.get('losing_trades', 0))),
            ("Win Rate", self._format_percentage(metrics.get('win_rate', 0))),
            ("Profit Factor", self._format_number(metrics.get('profit_factor', 0))),
            ("Avg Win", self._format_currency(metrics.get('avg_win', 0))),
            ("Avg Loss", self._format_currency(metrics.get('avg_loss', 0))),
            ("Largest Win", self._format_currency(metrics.get('largest_win', 0))),
            ("Largest Loss", self._format_currency(metrics.get('largest_loss', 0))),
        ]

        for metric, value in trading_metrics:
            table.add_row(metric, value)

        self.console.print(table)

    def _render_trade_history_table(self, trades: List[Dict[str, Any]]) -> None:
        """Render trade history table."""
        if not trades:
            self.console.print(Panel("No trade data available", title="📋 Trade History"))
            return

        table = Table(title="📋 Trade History", style="white")
        table.add_column("Date", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="blue", no_wrap=True)
        table.add_column("Side", style="magenta", no_wrap=True)
        table.add_column("Qty", style="yellow", justify="right")
        table.add_column("Entry", style="green", justify="right")
        table.add_column("Exit", style="green", justify="right")
        table.add_column("PnL", style="red", justify="right")
        table.add_column("Strategy", style="cyan", no_wrap=True)

        for trade in trades:
            entry_date = self._format_date(trade.get('entry_time'))
            symbol = str(trade.get('symbol', 'N/A'))
            side = str(trade.get('side', 'N/A'))
            quantity = str(trade.get('quantity', 0))
            entry_price = self._format_decimal(trade.get('entry_price'))
            exit_price = self._format_decimal(trade.get('exit_price', 'N/A'))
            pnl = self._format_currency(trade.get('pnl', 0))
            strategy = str(trade.get('strategy_name', 'N/A'))

            # Color code PnL
            pnl_value = trade.get('pnl', 0)
            if isinstance(pnl_value, (int, float, Decimal)) and pnl_value > 0:
                pnl = f"[green]{pnl}[/green]"
            elif isinstance(pnl_value, (int, float, Decimal)) and pnl_value < 0:
                pnl = f"[red]{pnl}[/red]"

            table.add_row(entry_date, symbol, side, quantity, entry_price, exit_price, pnl, strategy)

        self.console.print(table)

    def _render_equity_curve_summary(self, equity_curve: pd.Series) -> None:
        """Render equity curve summary."""
        if equity_curve.empty:
            self.console.print(Panel("No equity curve data available", title="📈 Equity Curve"))
            return

        starting_value = equity_curve.iloc[0]
        ending_value = equity_curve.iloc[-1]
        peak_value = equity_curve.max()
        trough_value = equity_curve.min()

        total_return = (ending_value - starting_value) / starting_value
        peak_date = equity_curve.idxmax()
        trough_date = equity_curve.idxmin()

        summary_text = f"""
        Starting Value: {self._format_currency(starting_value)}
        Ending Value: {self._format_currency(ending_value)}
        Peak Value: {self._format_currency(peak_value)} ({self._format_date(peak_date)})
        Trough Value: {self._format_currency(trough_value)} ({self._format_date(trough_date)})
        Total Return: {self._format_percentage(total_return)}
        """

        panel = Panel(
            summary_text.strip(),
            title="📈 Equity Curve Summary",
            border_style="green"
        )
        self.console.print(panel)

    def _render_strategy_attribution_table(self, strategy_performance: Dict[str, Dict]) -> None:
        """Render strategy performance attribution table."""
        if not strategy_performance:
            self.console.print(Panel("No strategy data available", title="🎯 Strategy Performance"))
            return

        table = Table(title="🎯 Strategy Performance Attribution", style="blue")
        table.add_column("Strategy", style="cyan", no_wrap=True)
        table.add_column("PnL", style="green", justify="right")
        table.add_column("Trades", style="yellow", justify="right")
        table.add_column("Win Rate", style="magenta", justify="right")
        table.add_column("Sharpe", style="blue", justify="right")

        for strategy_name, performance in strategy_performance.items():
            pnl = self._format_currency(performance.get('total_pnl', 0))
            trades = str(performance.get('trades', 0))
            win_rate = self._format_percentage(performance.get('win_rate', 0))
            sharpe = self._format_number(performance.get('sharpe_ratio', 0))

            table.add_row(strategy_name, pnl, trades, win_rate, sharpe)

        self.console.print(table)

    def _format_percentage(self, value: Any) -> str:
        """Format value as percentage."""
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.2%}"
        except (ValueError, TypeError):
            return "N/A"

    def _format_number(self, value: Any) -> str:
        """Format value as number with 2 decimal places."""
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.2f}"
        except (ValueError, TypeError):
            return "N/A"

    def _format_currency(self, value: Any) -> str:
        """Format value as currency."""
        if value is None:
            return "N/A"
        try:
            return f"${float(value):,.2f}"
        except (ValueError, TypeError):
            return "N/A"

    def _format_decimal(self, value: Any) -> str:
        """Format Decimal or numeric value."""
        if value is None or value == 'N/A':
            return "N/A"
        try:
            if isinstance(value, Decimal):
                return str(value)
            return f"{float(value):.2f}"
        except (ValueError, TypeError):
            return "N/A"

    def _format_date(self, value: Any) -> str:
        """Format datetime value."""
        if value is None:
            return "N/A"
        try:
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d")
            elif hasattr(value, 'strftime'):  # pandas Timestamp
                return value.strftime("%Y-%m-%d")
            return str(value)
        except (ValueError, TypeError, AttributeError):
            return "N/A"