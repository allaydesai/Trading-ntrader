/**
 * NTrader Statistics Module
 *
 * Displays trade statistics and drawdown metrics for backtests.
 * Renders tabular data with formatting for financial metrics.
 *
 * Dependencies: charts-core.js (must be loaded first)
 *
 * @module charts-statistics
 * @version 1.0.0
 */

/**
 * Fetches trade statistics from the API
 *
 * @param {string} backtestId - Backtest UUID
 * @returns {Promise<Object>} Trade statistics data
 * @throws {Error} If API request fails
 */
async function fetchTradeStatistics(backtestId) {
    const response = await fetch(`/api/statistics/${backtestId}`);
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return await response.json();
}

/**
 * Fetches drawdown metrics from the API
 *
 * @param {string} backtestId - Backtest UUID
 * @returns {Promise<Object>} Drawdown metrics data
 * @throws {Error} If API request fails
 */
async function fetchDrawdownMetrics(backtestId) {
    const response = await fetch(`/api/drawdown/${backtestId}`);
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return await response.json();
}

/**
 * Generates HTML for trade counts section
 *
 * @param {Object} stats - Trade statistics object
 * @returns {string} HTML string for trade counts card
 */
function renderTradeCountsCard(stats) {
    return `
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
    `;
}

/**
 * Generates HTML for performance metrics section
 *
 * @param {Object} stats - Trade statistics object
 * @returns {string} HTML string for performance card
 */
function renderPerformanceCard(stats) {
    return `
        <div class="bg-slate-800 rounded p-4">
            <h3 class="text-sm text-slate-400 mb-2">Performance</h3>
            <div class="space-y-1">
                <div class="flex justify-between text-sm">
                    <span class="text-slate-300">Win Rate:</span>
                    <span class="font-semibold">${stats.win_rate}%</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-slate-300">Profit Factor:</span>
                    <span class="font-semibold">${stats.profit_factor || "N/A"}</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-slate-300">Expectancy:</span>
                    <span class="font-semibold">${formatCurrency(stats.expectancy)}</span>
                </div>
            </div>
        </div>
    `;
}

/**
 * Generates HTML for profit/loss metrics section
 *
 * @param {Object} stats - Trade statistics object
 * @returns {string} HTML string for profit card
 */
function renderProfitCard(stats) {
    const profitClass = parseFloat(stats.net_profit) >= 0 ? "text-green-400" : "text-red-400";
    return `
        <div class="bg-slate-800 rounded p-4">
            <h3 class="text-sm text-slate-400 mb-2">Profit/Loss</h3>
            <div class="space-y-1">
                <div class="flex justify-between text-sm">
                    <span class="text-slate-300">Net Profit:</span>
                    <span class="font-semibold ${profitClass}">${formatCurrency(stats.net_profit)}</span>
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
    `;
}

/**
 * Generates HTML for streaks and holding period section
 *
 * @param {Object} stats - Trade statistics object
 * @returns {string} HTML string for streaks card
 */
function renderStreaksCard(stats) {
    return `
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
    `;
}

/**
 * Renders complete trade statistics HTML
 *
 * @param {Object} stats - Trade statistics object
 * @returns {string} Complete HTML grid for all statistics
 */
function renderStatisticsHTML(stats) {
    return `
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            ${renderTradeCountsCard(stats)}
            ${renderPerformanceCard(stats)}
            ${renderProfitCard(stats)}
            ${renderStreaksCard(stats)}
        </div>
    `;
}

/**
 * Initializes trade statistics display
 *
 * @param {HTMLElement} container - Container element with data-backtest-id
 * @returns {Promise<void>}
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
        const stats = await fetchTradeStatistics(backtestId);

        contentEl.innerHTML = renderStatisticsHTML(stats);
        loadingEl.classList.add("hidden");
        contentEl.classList.remove("hidden");

    } catch (error) {
        console.error("Error loading trade statistics:", error);
        loadingEl.classList.add("hidden");
        errorEl.classList.remove("hidden");
    }
}

/**
 * Renders the max drawdown card HTML
 *
 * @param {Object} maxDD - Maximum drawdown object
 * @returns {string} HTML string for max drawdown card
 */
function renderMaxDrawdownCard(maxDD) {
    const recoveryLine = maxDD.recovery_timestamp
        ? `<div>Recovery: ${formatTimestamp(maxDD.recovery_timestamp)}</div>`
        : "";

    return `
        <div class="bg-slate-800 rounded p-4 mb-4">
            <h3 class="text-sm text-slate-400 mb-3">Maximum Drawdown</h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                    <div class="flex justify-between text-sm mb-2">
                        <span class="text-slate-300">Drawdown:</span>
                        <span class="font-bold text-red-400 text-lg">${parseFloat(maxDD.drawdown_pct).toFixed(2)}%</span>
                    </div>
                    <div class="flex justify-between text-sm mb-1">
                        <span class="text-slate-300">Amount:</span>
                        <span class="font-semibold text-red-400">${formatCurrency(maxDD.drawdown_amount)}</span>
                    </div>
                    <div class="flex justify-between text-sm">
                        <span class="text-slate-300">Duration:</span>
                        <span class="font-semibold">${maxDD.duration_days} day${maxDD.duration_days !== 1 ? "s" : ""}</span>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-sm mb-1">
                        <span class="text-slate-300">Peak Balance:</span>
                        <span class="font-semibold">${formatCurrency(maxDD.peak_balance)}</span>
                    </div>
                    <div class="flex justify-between text-sm mb-1">
                        <span class="text-slate-300">Trough Balance:</span>
                        <span class="font-semibold text-red-400">${formatCurrency(maxDD.trough_balance)}</span>
                    </div>
                    <div class="flex justify-between text-sm">
                        <span class="text-slate-300">Status:</span>
                        <span class="font-semibold ${maxDD.recovered ? "text-green-400" : "text-yellow-400"}">
                            ${maxDD.recovered ? "Recovered" : "Ongoing"}
                        </span>
                    </div>
                </div>
            </div>
            <div class="mt-3 pt-3 border-t border-slate-700">
                <div class="text-xs text-slate-400 space-y-1">
                    <div>Peak: ${formatTimestamp(maxDD.peak_timestamp)}</div>
                    <div>Trough: ${formatTimestamp(maxDD.trough_timestamp)}</div>
                    ${recoveryLine}
                </div>
            </div>
        </div>
    `;
}

/**
 * Renders current (ongoing) drawdown card HTML
 *
 * @param {Object} currDD - Current drawdown object
 * @returns {string} HTML string for current drawdown card
 */
function renderCurrentDrawdownCard(currDD) {
    return `
        <div class="bg-slate-800 rounded p-4 mb-4 border-l-4 border-yellow-500">
            <h3 class="text-sm text-slate-400 mb-3">Current Drawdown (Ongoing)</h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                    <div class="flex justify-between text-sm mb-2">
                        <span class="text-slate-300">Drawdown:</span>
                        <span class="font-bold text-yellow-400 text-lg">${parseFloat(currDD.drawdown_pct).toFixed(2)}%</span>
                    </div>
                    <div class="flex justify-between text-sm">
                        <span class="text-slate-300">Amount:</span>
                        <span class="font-semibold text-yellow-400">${formatCurrency(currDD.drawdown_amount)}</span>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-sm mb-1">
                        <span class="text-slate-300">Peak Balance:</span>
                        <span class="font-semibold">${formatCurrency(currDD.peak_balance)}</span>
                    </div>
                    <div class="flex justify-between text-sm">
                        <span class="text-slate-300">Current Balance:</span>
                        <span class="font-semibold text-yellow-400">${formatCurrency(currDD.trough_balance)}</span>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Renders a single drawdown row in the top drawdowns list
 *
 * @param {Object} dd - Drawdown object
 * @param {number} index - Index in list (0-based)
 * @param {boolean} isLast - Whether this is the last item
 * @returns {string} HTML string for drawdown row
 */
function renderDrawdownRow(dd, index, isLast) {
    const borderClass = !isLast ? "border-b border-slate-700" : "";
    return `
        <div class="flex justify-between items-center text-sm py-2 ${borderClass}">
            <div class="flex items-center gap-3">
                <span class="text-slate-500 font-mono">#${index + 1}</span>
                <div>
                    <div class="font-semibold text-red-400">${parseFloat(dd.drawdown_pct).toFixed(2)}%</div>
                    <div class="text-xs text-slate-400">${formatCurrency(dd.drawdown_amount)}</div>
                </div>
            </div>
            <div class="text-right text-xs text-slate-400">
                <div>${dd.duration_days} day${dd.duration_days !== 1 ? "s" : ""}</div>
                <div>${dd.recovered ? "Recovered" : "Ongoing"}</div>
            </div>
        </div>
    `;
}

/**
 * Renders the top drawdowns list card
 *
 * @param {Array<Object>} drawdowns - Array of top drawdown periods
 * @param {number} totalPeriods - Total number of drawdown periods
 * @returns {string} HTML string for top drawdowns card
 */
function renderTopDrawdownsCard(drawdowns, totalPeriods) {
    const rows = drawdowns
        .map((dd, i) => renderDrawdownRow(dd, i, i === drawdowns.length - 1))
        .join("");

    return `
        <div class="bg-slate-800 rounded p-4">
            <h3 class="text-sm text-slate-400 mb-3">Top Drawdown Periods (${totalPeriods} total)</h3>
            <div class="space-y-2">
                ${rows}
            </div>
        </div>
    `;
}

/**
 * Renders "no drawdowns" placeholder
 *
 * @returns {string} HTML string for no-drawdowns state
 */
function renderNoDrawdowns() {
    return `
        <div class="text-center py-8">
            <svg class="h-12 w-12 mx-auto mb-3 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p class="text-slate-300 font-semibold mb-1">No Drawdowns Detected</p>
            <p class="text-sm text-slate-400">The equity curve shows consistent growth without any peak-to-trough periods.</p>
        </div>
    `;
}

/**
 * Initializes drawdown metrics display
 *
 * @param {HTMLElement} container - Container element with data-backtest-id
 * @returns {Promise<void>}
 */
async function initDrawdownMetrics(container) {
    const backtestId = container.dataset.backtestId;
    if (!backtestId) {
        console.error("Drawdown metrics: missing backtest ID");
        return;
    }

    const loadingEl = container.querySelector(".drawdown-loading");
    const contentEl = container.querySelector(".drawdown-content");
    const errorEl = container.querySelector(".drawdown-error");

    try {
        const metrics = await fetchDrawdownMetrics(backtestId);

        let html = "";

        if (metrics.max_drawdown) {
            html += renderMaxDrawdownCard(metrics.max_drawdown);

            // Current drawdown if different from max and ongoing
            if (metrics.current_drawdown &&
                !metrics.current_drawdown.recovered &&
                metrics.current_drawdown !== metrics.max_drawdown) {
                html += renderCurrentDrawdownCard(metrics.current_drawdown);
            }

            // Top drawdowns list
            if (metrics.top_drawdowns && metrics.top_drawdowns.length > 0) {
                html += renderTopDrawdownsCard(
                    metrics.top_drawdowns,
                    metrics.total_drawdown_periods
                );
            }
        } else {
            html = renderNoDrawdowns();
        }

        contentEl.innerHTML = html;
        loadingEl.classList.add("hidden");
        contentEl.classList.remove("hidden");

    } catch (error) {
        console.error("Error loading drawdown metrics:", error);
        loadingEl.classList.add("hidden");
        errorEl.classList.remove("hidden");
    }
}

// Export for global usage
if (typeof window !== "undefined") {
    window.initTradeStatistics = initTradeStatistics;
    window.initDrawdownMetrics = initDrawdownMetrics;
}
