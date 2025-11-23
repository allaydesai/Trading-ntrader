"""
Pydantic view models for backtest detail page.

Provides presentation-ready models for displaying comprehensive backtest metrics,
configuration snapshots, and trading summaries in Jinja2 templates.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


class MetricDisplayItem(BaseModel):
    """
    Single metric for display in metrics panel.

    Attributes:
        name: Human-readable metric name
        value: Numeric value (None if not available)
        format_type: How to format: "percentage", "decimal", "currency", "integer"
        tooltip: Explanation of what metric means
        is_favorable: Whether higher values are better (for color coding)

    Example:
        >>> metric = MetricDisplayItem(
        ...     name="Sharpe Ratio",
        ...     value=Decimal("1.67"),
        ...     format_type="decimal",
        ...     tooltip="Risk-adjusted return. Values > 1 are good.",
        ...     is_favorable=True
        ... )
        >>> print(metric.color_class)
        'text-green-400'
    """

    name: str = Field(..., description="Metric label")
    value: Optional[Decimal] = Field(None, description="Metric value")
    format_type: str = Field(..., description="percentage, decimal, currency, integer")
    tooltip: str = Field(..., description="Explanation text")
    is_favorable: bool = Field(True, description="Higher is better?")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def formatted_value(self) -> str:
        """Format value based on format_type."""
        if self.value is None:
            return "N/A"

        if self.format_type == "percentage":
            return f"{float(self.value) * 100:.2f}%"
        elif self.format_type == "decimal":
            return f"{float(self.value):.4f}"
        elif self.format_type == "currency":
            return f"${float(self.value):,.2f}"
        elif self.format_type == "integer":
            return f"{int(self.value):,}"
        return str(self.value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def color_class(self) -> str:
        """Tailwind CSS color class based on value."""
        if self.value is None:
            return "text-slate-400"

        if self.is_favorable:
            # Higher is better
            if self.value > 0:
                return "text-green-400"
            elif self.value < 0:
                return "text-red-400"
            return "text-slate-300"
        else:
            # Lower is better (e.g., drawdown)
            if self.value < 0:
                return "text-red-400"
            return "text-slate-300"


class MetricsPanel(BaseModel):
    """
    Organized metrics display for detail view.

    Attributes:
        return_metrics: Total return, CAGR, etc.
        risk_metrics: Sharpe, Sortino, Max Drawdown, etc.
        trading_metrics: Win rate, profit factor, trade counts

    Example:
        >>> panel = MetricsPanel(
        ...     return_metrics=[...],
        ...     risk_metrics=[...],
        ...     trading_metrics=[...]
        ... )
    """

    return_metrics: list[MetricDisplayItem] = Field(
        default_factory=list, description="Return-based metrics"
    )
    risk_metrics: list[MetricDisplayItem] = Field(
        default_factory=list, description="Risk-based metrics"
    )
    trading_metrics: list[MetricDisplayItem] = Field(
        default_factory=list, description="Trading statistics"
    )


class ConfigurationSnapshot(BaseModel):
    """
    Immutable configuration parameters for backtest.

    Attributes:
        instrument_symbol: Trading symbol (e.g., "AAPL")
        start_date: Backtest period start
        end_date: Backtest period end
        initial_capital: Starting balance
        strategy_name: Strategy identifier
        strategy_type: Strategy category
        data_source: Data provider
        additional_params: Strategy-specific parameters from config_snapshot

    Example:
        >>> config = ConfigurationSnapshot(
        ...     instrument_symbol="AAPL",
        ...     start_date=datetime(2024, 1, 1),
        ...     end_date=datetime(2024, 12, 31),
        ...     initial_capital=Decimal("1000000.00"),
        ...     strategy_name="SMA Crossover",
        ...     strategy_type="trend_following",
        ...     data_source="IBKR",
        ...     additional_params={"fast_period": 10, "slow_period": 50}
        ... )
        >>> print(config.cli_command)
    """

    instrument_symbol: str = Field(..., description="Trading symbol")
    start_date: datetime = Field(..., description="Backtest start date")
    end_date: datetime = Field(..., description="Backtest end date")
    initial_capital: Decimal = Field(..., description="Starting capital")
    strategy_name: str = Field(..., description="Strategy name")
    strategy_type: str = Field(..., description="Strategy category")
    data_source: str = Field(..., description="Data source")
    additional_params: dict = Field(default_factory=dict, description="Strategy-specific params")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def date_range_display(self) -> str:
        """Formatted date range."""
        return f"{self.start_date.date()} to {self.end_date.date()}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cli_command(self) -> str:
        """Generate CLI command to replicate this backtest."""
        cmd_parts = [
            "ntrader backtest run",
            f"--strategy {self.strategy_name}",
            f"--instrument {self.instrument_symbol}",
            f"--start {self.start_date.date()}",
            f"--end {self.end_date.date()}",
            f"--capital {self.initial_capital}",
        ]

        # Add strategy-specific parameters
        for key, value in self.additional_params.items():
            cmd_parts.append(f"--{key.replace('_', '-')} {value}")

        return " \\\n  ".join(cmd_parts)


class TradingSummary(BaseModel):
    """
    Summary of trading activity from metrics.

    Note: Individual trade records are not persisted in MVP.
    This summary is derived from PerformanceMetrics.

    Attributes:
        total_trades: Count of all trades
        winning_trades: Count of profitable trades
        losing_trades: Count of losing trades
        win_rate: Percentage of winning trades
        avg_win: Average profit on winning trades
        avg_loss: Average loss on losing trades
        profit_factor: Gross profit / gross loss
        expectancy: Expected profit per trade

    Example:
        >>> summary = TradingSummary(
        ...     total_trades=156,
        ...     winning_trades=98,
        ...     losing_trades=58,
        ...     win_rate=Decimal("0.6282"),
        ...     avg_win=Decimal("234.56"),
        ...     avg_loss=Decimal("-145.23"),
        ...     profit_factor=Decimal("2.34"),
        ...     expectancy=Decimal("89.50")
        ... )
    """

    total_trades: int = Field(..., ge=0, description="Total trades executed")
    winning_trades: int = Field(..., ge=0, description="Profitable trades")
    losing_trades: int = Field(..., ge=0, description="Losing trades")
    win_rate: Optional[Decimal] = Field(None, description="Win percentage (0-1)")
    avg_win: Optional[Decimal] = Field(None, description="Average winning trade")
    avg_loss: Optional[Decimal] = Field(None, description="Average losing trade")
    profit_factor: Optional[Decimal] = Field(None, description="Profit/loss ratio")
    expectancy: Optional[Decimal] = Field(None, description="Expected $ per trade")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_trades(self) -> bool:
        """Whether any trades were executed."""
        return self.total_trades > 0


class BacktestDetailView(BaseModel):
    """
    Complete detail view model for single backtest.

    Combines metrics, configuration, and metadata for template rendering.

    Attributes:
        id: Internal database ID (for API calls)
        run_id: Business identifier (UUID)
        strategy_name: Strategy name for display
        execution_status: "success" or "failed"
        execution_time: When backtest was run
        execution_duration: How long it took (seconds)
        error_message: Error details if failed
        metrics_panel: Organized performance metrics
        configuration: Parameters used for backtest
        trading_summary: Aggregated trade statistics
        breadcrumbs: Navigation path

    Example:
        >>> view = BacktestDetailView(
        ...     id=123,
        ...     run_id=UUID("..."),
        ...     strategy_name="SMA Crossover",
        ...     execution_status="success",
        ...     execution_time=datetime.now(),
        ...     execution_duration=Decimal("45.5"),
        ...     metrics_panel=MetricsPanel(...),
        ...     configuration=ConfigurationSnapshot(...),
        ...     trading_summary=TradingSummary(...)
        ... )
    """

    id: int = Field(..., description="Internal database ID")
    run_id: UUID = Field(..., description="Backtest identifier")
    strategy_name: str = Field(..., description="Strategy name")
    execution_status: str = Field(..., description="success or failed")
    execution_time: datetime = Field(..., description="When executed")
    execution_duration: Decimal = Field(..., description="Duration in seconds")
    error_message: Optional[str] = Field(default=None, description="Error if failed")

    metrics_panel: Optional[MetricsPanel] = Field(
        default=None, description="Performance metrics (None if failed)"
    )
    configuration: ConfigurationSnapshot = Field(..., description="Backtest parameters")
    trading_summary: Optional[TradingSummary] = Field(
        default=None, description="Trade statistics (None if failed)"
    )

    breadcrumbs: list[dict[str, str | None]] = Field(
        ...,
        description="Navigation breadcrumbs - must be constructed by caller",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def run_id_short(self) -> str:
        """First 8 characters of UUID."""
        return str(self.run_id)[:8]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_successful(self) -> bool:
        """Whether backtest completed successfully."""
        return self.execution_status == "success"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_formatted(self) -> str:
        """Human-readable duration."""
        seconds = float(self.execution_duration)
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = seconds / 60
        return f"{minutes:.1f}m"


# =============================================================================
# Mapping Functions: Database to View Models
# =============================================================================


def build_metrics_panel(metrics) -> Optional[MetricsPanel]:
    """
    Convert database PerformanceMetrics to a structured display panel.

    Transforms raw performance metrics from the database into an organized
    MetricsPanel with three categories: return metrics, risk metrics, and
    trading metrics. Each metric is formatted with appropriate display
    settings including tooltips and color coding indicators.

    Args:
        metrics: PerformanceMetrics ORM instance from database containing
            raw metric values (total_return, sharpe_ratio, max_drawdown, etc.).
            Can be None if backtest failed or has no metrics.

    Returns:
        MetricsPanel containing organized MetricDisplayItem lists for each
        category, or None if input metrics is None. The panel includes:
        - return_metrics: Total Return, CAGR, Final Balance
        - risk_metrics: Sharpe Ratio, Sortino Ratio, Max Drawdown, Volatility
        - trading_metrics: Total Trades, Win Rate, Profit Factor

    Example:
        >>> from decimal import Decimal
        >>> from src.db.models.backtest import PerformanceMetrics
        >>> metrics = PerformanceMetrics(
        ...     total_return=Decimal("0.25"),
        ...     cagr=Decimal("0.18"),
        ...     sharpe_ratio=Decimal("1.67"),
        ...     max_drawdown=Decimal("-0.12"),
        ...     total_trades=156,
        ...     win_rate=Decimal("0.628")
        ... )
        >>> panel = build_metrics_panel(metrics)
        >>> panel.return_metrics[0].name
        'Total Return'
        >>> panel.return_metrics[0].formatted_value
        '25.00%'
        >>> panel.risk_metrics[0].formatted_value
        '1.6700'
    """
    if metrics is None:
        return None

    # Validate required metrics fields exist
    required_attrs = [
        "total_return",
        "cagr",
        "final_balance",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "volatility",
        "total_trades",
        "win_rate",
        "profit_factor",
    ]

    missing_attrs = [attr for attr in required_attrs if not hasattr(metrics, attr)]
    if missing_attrs:
        raise ValueError(f"Metrics object missing required fields: {', '.join(missing_attrs)}")

    return MetricsPanel(
        return_metrics=[
            MetricDisplayItem(
                name="Total Return",
                value=metrics.total_return,
                format_type="percentage",
                tooltip="Total percentage gain/loss from initial capital",
                is_favorable=True,
            ),
            MetricDisplayItem(
                name="CAGR",
                value=metrics.cagr,
                format_type="percentage",
                tooltip="Compound Annual Growth Rate - annualized return",
                is_favorable=True,
            ),
            MetricDisplayItem(
                name="Final Balance",
                value=metrics.final_balance,
                format_type="currency",
                tooltip="Portfolio value at end of backtest",
                is_favorable=True,
            ),
        ],
        risk_metrics=[
            MetricDisplayItem(
                name="Sharpe Ratio",
                value=metrics.sharpe_ratio,
                format_type="decimal",
                tooltip="Risk-adjusted return. >1 is good, >2 is excellent",
                is_favorable=True,
            ),
            MetricDisplayItem(
                name="Sortino Ratio",
                value=metrics.sortino_ratio,
                format_type="decimal",
                tooltip="Like Sharpe but only penalizes downside volatility",
                is_favorable=True,
            ),
            MetricDisplayItem(
                name="Max Drawdown",
                value=metrics.max_drawdown,
                format_type="percentage",
                tooltip="Largest peak-to-trough decline (negative is worse)",
                is_favorable=False,
            ),
            MetricDisplayItem(
                name="Volatility",
                value=metrics.volatility,
                format_type="percentage",
                tooltip="Standard deviation of returns (risk measure)",
                is_favorable=False,
            ),
        ],
        trading_metrics=[
            MetricDisplayItem(
                name="Total Trades",
                value=Decimal(metrics.total_trades) if metrics.total_trades else None,
                format_type="integer",
                tooltip="Total number of trades executed",
                is_favorable=True,
            ),
            MetricDisplayItem(
                name="Win Rate",
                value=metrics.win_rate,
                format_type="percentage",
                tooltip="Percentage of profitable trades",
                is_favorable=True,
            ),
            MetricDisplayItem(
                name="Profit Factor",
                value=metrics.profit_factor,
                format_type="decimal",
                tooltip="Gross profit / gross loss. >1 is profitable",
                is_favorable=True,
            ),
        ],
    )


def build_configuration(run) -> ConfigurationSnapshot:
    """
    Extract configuration from backtest run.

    Args:
        run: BacktestRun database instance

    Returns:
        ConfigurationSnapshot for display

    Example:
        >>> from src.db.models.backtest import BacktestRun
        >>> run = BacktestRun(strategy_name="SMA Crossover", ...)
        >>> config = build_configuration(run)
        >>> print(config.strategy_name)
        'SMA Crossover'
    """
    return ConfigurationSnapshot(
        instrument_symbol=run.instrument_symbol,
        start_date=run.start_date,
        end_date=run.end_date,
        initial_capital=run.initial_capital,
        strategy_name=run.strategy_name,
        strategy_type=run.strategy_type,
        data_source=run.data_source,
        additional_params=run.config_snapshot or {},
    )


def build_trading_summary(metrics) -> Optional[TradingSummary]:
    """
    Build trading summary from metrics.

    Args:
        metrics: PerformanceMetrics from database

    Returns:
        TradingSummary or None if no metrics

    Example:
        >>> summary = build_trading_summary(metrics)
        >>> print(summary.has_trades)
        True
    """
    if metrics is None:
        return None

    return TradingSummary(
        total_trades=metrics.total_trades,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        win_rate=metrics.win_rate,
        avg_win=metrics.avg_win,
        avg_loss=metrics.avg_loss,
        profit_factor=metrics.profit_factor,
        expectancy=metrics.expectancy,
    )


def to_detail_view(run, base_url: str = "") -> BacktestDetailView:
    """
    Map BacktestRun to complete detail view model.

    Args:
        run: BacktestRun with metrics eagerly loaded
        base_url: Base URL prefix for breadcrumb links (default: "")

    Returns:
        BacktestDetailView ready for template rendering

    Example:
        >>> run = await repository.find_by_run_id(run_id)
        >>> view = to_detail_view(run)
        >>> return templates.TemplateResponse("detail.html", {"view": view})
    """
    return BacktestDetailView(
        id=run.id,
        run_id=run.run_id,
        strategy_name=run.strategy_name,
        execution_status=run.execution_status,
        execution_time=run.created_at,
        execution_duration=run.execution_duration_seconds,
        error_message=run.error_message,
        metrics_panel=build_metrics_panel(run.metrics),
        configuration=build_configuration(run),
        trading_summary=build_trading_summary(run.metrics),
        breadcrumbs=[
            {"label": "Dashboard", "url": f"{base_url}/"},
            {"label": "Backtests", "url": f"{base_url}/backtests"},
            {"label": "Run Details", "url": None},
        ],
    )
