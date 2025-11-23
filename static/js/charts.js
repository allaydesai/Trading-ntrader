/**
 * NTrader Interactive Charts
 *
 * Implements TradingView Lightweight Charts for backtest visualization.
 * Consumes Chart APIs (Phase 4) to display candlestick charts with
 * indicators, trade markers, and equity curves.
 */

// Chart color configuration (matches Tailwind dark theme)
const CHART_COLORS = {
    background: "#020617",      // slate-950
    text: "#e5e7eb",            // slate-100
    gridLines: "#1e293b",       // slate-800
    bullish: "#22c55e",         // green-500
    bearish: "#ef4444",         // red-500
    smaFast: "#3b82f6",         // blue-500
    smaSlow: "#f59e0b",         // amber-500
    equity: "#22c55e",          // green-500
    drawdown: "#ef4444",        // red-500
    volume: "#475569",          // slate-600
};

/**
 * Initialize all charts on page load
 */
function initCharts() {
    const chartDivs = document.querySelectorAll("[data-chart]");
    chartDivs.forEach((el) => {
        const chartType = el.dataset.chart;
        if (chartType === "run-price") {
            initRunPriceChart(el);
        } else if (chartType === "run-equity") {
            initEquityChart(el);
        } else if (chartType === "data-view") {
            initDataViewChart(el);
        }
    });
}

/**
 * Handle HTMX swaps - reinitialize charts in swapped content
 */
function handleHtmxSwap(evt) {
    const el = evt.target;
    const chartDivs = el.querySelectorAll("[data-chart]");
    chartDivs.forEach((child) => {
        const chartType = child.dataset.chart;
        if (chartType === "run-price") {
            initRunPriceChart(child);
        } else if (chartType === "run-equity") {
            initEquityChart(child);
        } else if (chartType === "data-view") {
            initDataViewChart(child);
        }
    });
}

/**
 * Create chart with default dark theme options
 */
function createChartWithDefaults(container) {
    return LightweightCharts.createChart(container, {
        layout: {
            background: { type: "solid", color: CHART_COLORS.background },
            textColor: CHART_COLORS.text,
        },
        grid: {
            vertLines: { color: CHART_COLORS.gridLines },
            horzLines: { color: CHART_COLORS.gridLines },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            borderColor: CHART_COLORS.gridLines,
        },
        rightPriceScale: {
            borderColor: CHART_COLORS.gridLines,
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
    });
}

/**
 * Hide loading spinner for a chart container
 */
function hideLoading(container) {
    const loadingEl = container.querySelector(".chart-loading");
    if (loadingEl) {
        loadingEl.style.display = "none";
    }
}

/**
 * Show error message in chart container
 */
function showError(container, message) {
    hideLoading(container);
    const errorDiv = document.createElement("div");
    errorDiv.className = "flex items-center justify-center h-full text-red-400 text-sm";
    errorDiv.innerHTML = `
        <div class="text-center">
            <svg class="h-8 w-8 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <p>${message}</p>
        </div>
    `;
    container.appendChild(errorDiv);
}

/**
 * Initialize price chart with candlesticks, indicators, and trade markers
 */
async function initRunPriceChart(container) {
    const runId = container.dataset.runId;
    const symbol = container.dataset.symbol;
    const start = container.dataset.start;
    const end = container.dataset.end;

    try {
        // Fetch all data in parallel
        const [timeseriesResp, tradesResp, indicatorsResp] = await Promise.all([
            fetch(`/api/timeseries?symbol=${symbol}&start=${start}&end=${end}&timeframe=1_DAY`),
            fetch(`/api/trades/${runId}`),
            fetch(`/api/indicators/${runId}`),
        ]);

        // Check for errors
        if (!timeseriesResp.ok) {
            const error = await timeseriesResp.json();
            throw new Error(error.detail || "Failed to load price data");
        }

        const timeseriesData = await timeseriesResp.json();
        const tradesData = tradesResp.ok ? await tradesResp.json() : { trades: [] };
        const indicatorsData = indicatorsResp.ok ? await indicatorsResp.json() : { indicators: {} };

        // Hide loading spinner
        hideLoading(container);

        // Check for empty data
        if (!timeseriesData.candles || timeseriesData.candles.length === 0) {
            showError(container, "No price data available for this date range");
            return;
        }

        // Create chart
        const chart = createChartWithDefaults(container);

        // Add candlestick series
        const candlestickSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: CHART_COLORS.bullish,
            downColor: CHART_COLORS.bearish,
            borderVisible: false,
            wickUpColor: CHART_COLORS.bullish,
            wickDownColor: CHART_COLORS.bearish,
        });

        // Format candle data for TradingView, deduplicate and sort by timestamp
        const seenTimes = new Set();
        const candleData = timeseriesData.candles
            .filter((c) => {
                if (seenTimes.has(c.time)) {
                    return false;
                }
                seenTimes.add(c.time);
                return true;
            })
            .map((c) => ({
                time: c.time,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
            }))
            .sort((a, b) => a.time - b.time);
        candlestickSeries.setData(candleData);

        // Add volume series
        const volumeSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
            color: CHART_COLORS.volume,
            priceFormat: {
                type: "volume",
            },
            priceScaleId: "",
        });
        volumeSeries.priceScale().applyOptions({
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });
        // Deduplicate and sort volume data
        const seenVolumeTimes = new Set();
        const volumeData = timeseriesData.candles
            .filter((c) => {
                if (seenVolumeTimes.has(c.time)) {
                    return false;
                }
                seenVolumeTimes.add(c.time);
                return true;
            })
            .map((c) => ({
                time: c.time,
                value: c.volume,
                color: c.close >= c.open ? CHART_COLORS.bullish + "60" : CHART_COLORS.bearish + "60",
            }))
            .sort((a, b) => a.time - b.time);
        volumeSeries.setData(volumeData);

        // Add indicator series if available
        if (indicatorsData.indicators) {
            // SMA Fast
            if (indicatorsData.indicators.sma_fast && indicatorsData.indicators.sma_fast.length > 0) {
                const smaFastSeries = chart.addSeries(LightweightCharts.LineSeries, {
                    color: CHART_COLORS.smaFast,
                    lineWidth: 1,
                    title: "SMA Fast",
                });
                const smaFastData = indicatorsData.indicators.sma_fast.map((p) => ({
                    time: p.time,
                    value: p.value,
                }));
                smaFastSeries.setData(smaFastData);
            }

            // SMA Slow
            if (indicatorsData.indicators.sma_slow && indicatorsData.indicators.sma_slow.length > 0) {
                const smaSlowSeries = chart.addSeries(LightweightCharts.LineSeries, {
                    color: CHART_COLORS.smaSlow,
                    lineWidth: 1,
                    title: "SMA Slow",
                });
                const smaSlowData = indicatorsData.indicators.sma_slow.map((p) => ({
                    time: p.time,
                    value: p.value,
                }));
                smaSlowSeries.setData(smaSlowData);
            }
        }

        // Add trade markers
        if (tradesData.trades && tradesData.trades.length > 0) {
            const markers = tradesData.trades.map((t) => ({
                time: t.time,
                position: t.side === "buy" ? "belowBar" : "aboveBar",
                color: t.side === "buy" ? CHART_COLORS.bullish : CHART_COLORS.bearish,
                shape: t.side === "buy" ? "arrowUp" : "arrowDown",
                text: t.side === "buy" ? "B" : "S",
            }));
            candlestickSeries.setMarkers(markers);
        }

        // Fit content to show all data
        chart.timeScale().fitContent();

        // Handle container resize
        const resizeObserver = new ResizeObserver(() => {
            chart.applyOptions({
                width: container.clientWidth,
                height: container.clientHeight,
            });
        });
        resizeObserver.observe(container);

    } catch (error) {
        console.error("Error initializing price chart:", error);
        showError(container, error.message || "Failed to load chart");
    }
}

/**
 * Initialize equity curve chart based on individual trade tracking
 */
async function initEquityChart(container) {
    const runId = container.dataset.runId;
    const backtestId = container.dataset.backtestId;

    try {
        // Use backtest_id if available (new trade tracking), fallback to run_id (legacy)
        const endpoint = backtestId
            ? `/api/equity-curve/${backtestId}`
            : `/api/equity/${runId}`;

        const response = await fetch(endpoint);

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || "Failed to load equity data");
        }

        const data = await response.json();

        // Hide loading spinner
        hideLoading(container);

        // Handle new equity curve format (from trade tracking)
        if (data.points) {
            // Check for empty data
            if (data.points.length === 0) {
                showError(container, "No trades executed - equity curve unavailable");
                return;
            }

            // Create chart
            const chart = createChartWithDefaults(container);

            // Add equity line series
            const equitySeries = chart.addSeries(LightweightCharts.LineSeries, {
                color: CHART_COLORS.equity,
                lineWidth: 2,
                title: "Account Balance",
            });

            // Convert EquityCurvePoint[] to chart format
            // Timestamps are ISO 8601 strings, convert to Unix timestamps
            const equityData = data.points.map((point) => {
                const timestamp = new Date(point.timestamp);
                return {
                    time: Math.floor(timestamp.getTime() / 1000), // Unix timestamp in seconds
                    value: parseFloat(point.balance),
                };
            });
            equitySeries.setData(equityData);

            // Add summary info as chart title/watermark
            const summaryText = `Initial: $${parseFloat(data.initial_capital).toFixed(2)} | ` +
                               `Final: $${parseFloat(data.final_balance).toFixed(2)} | ` +
                               `Return: ${parseFloat(data.total_return_pct).toFixed(2)}%`;

            chart.applyOptions({
                watermark: {
                    visible: true,
                    fontSize: 12,
                    horzAlign: "left",
                    vertAlign: "top",
                    color: CHART_COLORS.text + "40",
                    text: summaryText,
                },
            });

            // Fit content to show all data
            chart.timeScale().fitContent();

            // Handle container resize
            const resizeObserver = new ResizeObserver(() => {
                chart.applyOptions({
                    width: container.clientWidth,
                    height: container.clientHeight,
                });
            });
            resizeObserver.observe(container);

        } else if (data.equity) {
            // Legacy format - handle old equity endpoint
            if (data.equity.length === 0) {
                showError(container, "No equity data available");
                return;
            }

            const chart = createChartWithDefaults(container);

            const equitySeries = chart.addSeries(LightweightCharts.LineSeries, {
                color: CHART_COLORS.equity,
                lineWidth: 2,
                title: "Equity",
            });
            const equityData = data.equity.map((p) => ({
                time: p.time,
                value: p.value,
            }));
            equitySeries.setData(equityData);

            // Add drawdown area series if available
            if (data.drawdown && data.drawdown.length > 0) {
                const drawdownSeries = chart.addSeries(LightweightCharts.AreaSeries, {
                    topColor: CHART_COLORS.bearish + "40",
                    bottomColor: CHART_COLORS.bearish + "10",
                    lineColor: CHART_COLORS.bearish,
                    lineWidth: 1,
                    title: "Drawdown %",
                    priceScaleId: "drawdown",
                });

                chart.priceScale("drawdown").applyOptions({
                    scaleMargins: {
                        top: 0.7,
                        bottom: 0,
                    },
                });

                const drawdownData = data.drawdown.map((p) => ({
                    time: p.time,
                    value: Math.abs(p.value),
                }));
                drawdownSeries.setData(drawdownData);
            }

            chart.timeScale().fitContent();

            const resizeObserver = new ResizeObserver(() => {
                chart.applyOptions({
                    width: container.clientWidth,
                    height: container.clientHeight,
                });
            });
            resizeObserver.observe(container);
        } else {
            throw new Error("Invalid equity curve response format");
        }

    } catch (error) {
        console.error("Error initializing equity chart:", error);
        showError(container, error.message || "Failed to load chart");
    }
}

/**
 * Initialize data view chart (for data catalog page)
 */
async function initDataViewChart(container) {
    const symbol = container.dataset.symbol;
    const start = container.dataset.start;
    const end = container.dataset.end;
    const timeframe = container.dataset.timeframe || "1_DAY";

    try {
        const response = await fetch(
            `/api/timeseries?symbol=${symbol}&start=${start}&end=${end}&timeframe=${timeframe}`
        );

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || "Failed to load data");
        }

        const data = await response.json();

        // Hide loading spinner
        hideLoading(container);

        // Check for empty data
        if (!data.candles || data.candles.length === 0) {
            showError(container, "No data available for this selection");
            return;
        }

        // Create chart
        const chart = createChartWithDefaults(container);

        // Add candlestick series
        const candlestickSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: CHART_COLORS.bullish,
            downColor: CHART_COLORS.bearish,
            borderVisible: false,
            wickUpColor: CHART_COLORS.bullish,
            wickDownColor: CHART_COLORS.bearish,
        });

        const candleData = data.candles.map((c) => ({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
        }));
        candlestickSeries.setData(candleData);

        // Add volume series
        const volumeSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
            color: CHART_COLORS.volume,
            priceFormat: {
                type: "volume",
            },
            priceScaleId: "",
        });
        volumeSeries.priceScale().applyOptions({
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });
        const volumeData = data.candles.map((c) => ({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? CHART_COLORS.bullish + "60" : CHART_COLORS.bearish + "60",
        }));
        volumeSeries.setData(volumeData);

        // Fit content
        chart.timeScale().fitContent();

        // Handle resize
        const resizeObserver = new ResizeObserver(() => {
            chart.applyOptions({
                width: container.clientWidth,
                height: container.clientHeight,
            });
        });
        resizeObserver.observe(container);

    } catch (error) {
        console.error("Error initializing data view chart:", error);
        showError(container, error.message || "Failed to load chart");
    }
}

/**
 * Format currency value for display
 * @param {string|number} value - Numeric value to format
 * @returns {string} Formatted currency string
 */
function formatCurrency(value) {
    const num = parseFloat(value);
    if (isNaN(num)) return '$0.00';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(num);
}

/**
 * Initialize trade statistics display
 * @param {HTMLElement} container - Container element with data-backtest-id
 */
async function initTradeStatistics(container) {
    const backtestId = container.dataset.backtestId;
    if (!backtestId) {
        console.error("Trade statistics: missing backtest ID");
        return;
    }

    const loadingEl = container.querySelector(".stats-loading");
    const contentEl = container.querySelector(".stats-content");
    const errorEl = container.querySelector(".stats-error");

    try {
        // Fetch statistics from API
        const response = await fetch(`/api/statistics/${backtestId}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const stats = await response.json();

        // Build statistics HTML
        const html = `
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <!-- Trade Counts -->
                <div class="bg-slate-800 rounded p-4">
                    <h3 class="text-sm text-slate-400 mb-2">Trade Counts</h3>
                    <div class="space-y-1">
                        <div class="flex justify-between text-sm">
                            <span class="text-slate-300">Total:</span>
                            <span class="font-semibold">${stats.total_trades}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-green-400">Wins:</span>
                            <span class="font-semibold text-green-400">${stats.winning_trades}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-red-400">Losses:</span>
                            <span class="font-semibold text-red-400">${stats.losing_trades}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-yellow-400">Breakeven:</span>
                            <span class="font-semibold text-yellow-400">${stats.breakeven_trades}</span>
                        </div>
                    </div>
                </div>

                <!-- Win Rate & Profit Factor -->
                <div class="bg-slate-800 rounded p-4">
                    <h3 class="text-sm text-slate-400 mb-2">Performance</h3>
                    <div class="space-y-1">
                        <div class="flex justify-between text-sm">
                            <span class="text-slate-300">Win Rate:</span>
                            <span class="font-semibold">${stats.win_rate}%</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-slate-300">Profit Factor:</span>
                            <span class="font-semibold">${stats.profit_factor || 'N/A'}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-slate-300">Expectancy:</span>
                            <span class="font-semibold">${formatCurrency(stats.expectancy)}</span>
                        </div>
                    </div>
                </div>

                <!-- Profit Metrics -->
                <div class="bg-slate-800 rounded p-4">
                    <h3 class="text-sm text-slate-400 mb-2">Profit/Loss</h3>
                    <div class="space-y-1">
                        <div class="flex justify-between text-sm">
                            <span class="text-slate-300">Net Profit:</span>
                            <span class="font-semibold ${parseFloat(stats.net_profit) >= 0 ? 'text-green-400' : 'text-red-400'}">${formatCurrency(stats.net_profit)}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-green-400">Avg Win:</span>
                            <span class="font-semibold text-green-400">${formatCurrency(stats.average_win)}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-red-400">Avg Loss:</span>
                            <span class="font-semibold text-red-400">${formatCurrency(stats.average_loss)}</span>
                        </div>
                    </div>
                </div>

                <!-- Streaks & Holding Period -->
                <div class="bg-slate-800 rounded p-4">
                    <h3 class="text-sm text-slate-400 mb-2">Streaks & Time</h3>
                    <div class="space-y-1">
                        <div class="flex justify-between text-sm">
                            <span class="text-green-400">Max Win Streak:</span>
                            <span class="font-semibold text-green-400">${stats.max_consecutive_wins}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-red-400">Max Loss Streak:</span>
                            <span class="font-semibold text-red-400">${stats.max_consecutive_losses}</span>
                        </div>
                        <div class="flex justify-between text-sm">
                            <span class="text-slate-300">Avg Hold:</span>
                            <span class="font-semibold">${stats.avg_holding_period_hours}h</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Show content
        contentEl.innerHTML = html;
        loadingEl.classList.add("hidden");
        contentEl.classList.remove("hidden");

    } catch (error) {
        console.error("Error loading trade statistics:", error);
        loadingEl.classList.add("hidden");
        errorEl.classList.remove("hidden");
    }
}

// Initialize on DOM ready
document.addEventListener("DOMContentLoaded", () => {
    initCharts();

    // Initialize trade statistics
    const statsContainer = document.getElementById("trade-statistics");
    if (statsContainer) {
        initTradeStatistics(statsContainer);
    }
});

// Reinitialize after HTMX swaps
document.body.addEventListener("htmx:afterSwap", handleHtmxSwap);
