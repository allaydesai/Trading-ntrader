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
 * Tailwind text class for a signed P&L value.
 * Red/green is reserved for signed profit/loss meaning; zero and
 * non-numeric values render in neutral ink (DESIGN.md Semantic Color Rule).
 *
 * @param {string|number} value - Signed numeric value
 * @returns {string} Tailwind text color class
 */
function pnlClass(value) {
    const v = parseFloat(value);
    if (!isFinite(v) || v === 0) return "text-slate-100";
    return v > 0 ? "text-green-400" : "text-red-400";
}

/**
 * Generates HTML for trade counts section
 *
 * @param {Object} stats - Trade statistics object
 * @returns {string} HTML string for trade counts card
 */
function renderTradeCountsCard(stats) {
    return `
        <div>
            <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider border-b border-slate-800 pb-2 mb-3">Trade Counts</h3>
            <div class="space-y-2">
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Total</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.total_trades}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Wins</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.winning_trades}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Losses</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.losing_trades}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Breakeven</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.breakeven_trades}</span>
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
        <div>
            <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider border-b border-slate-800 pb-2 mb-3">Performance</h3>
            <div class="space-y-2">
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Win Rate</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.win_rate}%</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Profit Factor</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.profit_factor || "N/A"}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Expectancy</span>
                    <span class="font-mono font-semibold ${pnlClass(stats.expectancy)} tabular-nums">${formatCurrency(stats.expectancy)}</span>
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
    return `
        <div>
            <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider border-b border-slate-800 pb-2 mb-3">Profit/Loss</h3>
            <div class="space-y-2">
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Net Profit</span>
                    <span class="font-mono font-semibold ${pnlClass(stats.net_profit)} tabular-nums">${formatCurrency(stats.net_profit)}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Avg Win</span>
                    <span class="font-mono font-semibold text-green-400 tabular-nums">${formatCurrency(stats.average_win)}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Avg Loss</span>
                    <span class="font-mono font-semibold text-red-400 tabular-nums">${formatCurrency(stats.average_loss)}</span>
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
        <div>
            <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider border-b border-slate-800 pb-2 mb-3">Streaks &amp; Time</h3>
            <div class="space-y-2">
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Max Win Streak</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.max_consecutive_wins}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Max Loss Streak</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.max_consecutive_losses}</span>
                </div>
                <div class="flex justify-between items-baseline text-sm">
                    <span class="text-slate-400">Avg Hold</span>
                    <span class="font-mono font-semibold text-slate-100 tabular-nums">${stats.avg_holding_period_hours}h</span>
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
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-x-8 gap-y-6">
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
    const statusBadge = maxDD.recovered
        ? '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-900 text-green-400">Recovered</span>'
        : '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-900 text-amber-400">Ongoing</span>';

    return `
        <div class="mb-6">
            <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider border-b border-slate-800 pb-2 mb-3">Maximum Drawdown</h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
                <div class="space-y-2">
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Drawdown</span>
                        <span class="font-mono font-semibold text-red-400 tabular-nums">${parseFloat(maxDD.drawdown_pct).toFixed(2)}%</span>
                    </div>
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Amount</span>
                        <span class="font-mono font-semibold text-red-400 tabular-nums">${formatCurrency(maxDD.drawdown_amount)}</span>
                    </div>
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Duration</span>
                        <span class="font-mono font-semibold text-slate-100 tabular-nums">${maxDD.duration_days} day${maxDD.duration_days !== 1 ? "s" : ""}</span>
                    </div>
                </div>
                <div class="space-y-2">
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Peak Balance</span>
                        <span class="font-mono font-semibold text-slate-100 tabular-nums">${formatCurrency(maxDD.peak_balance)}</span>
                    </div>
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Trough Balance</span>
                        <span class="font-mono font-semibold text-slate-100 tabular-nums">${formatCurrency(maxDD.trough_balance)}</span>
                    </div>
                    <div class="flex justify-between items-center text-sm">
                        <span class="text-slate-400">Status</span>
                        ${statusBadge}
                    </div>
                </div>
            </div>
            <div class="mt-3 pt-3 border-t border-slate-800">
                <div class="font-mono text-xs text-slate-400 space-y-1">
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
        <div class="bg-yellow-900/10 border border-yellow-900 rounded-md p-4 mb-6">
            <div class="flex items-center justify-between border-b border-yellow-900/60 pb-2 mb-3">
                <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider">Current Drawdown</h3>
                <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-900 text-amber-400">Ongoing</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
                <div class="space-y-2">
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Drawdown</span>
                        <span class="font-mono font-semibold text-amber-400 tabular-nums">${parseFloat(currDD.drawdown_pct).toFixed(2)}%</span>
                    </div>
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Amount</span>
                        <span class="font-mono font-semibold text-amber-400 tabular-nums">${formatCurrency(currDD.drawdown_amount)}</span>
                    </div>
                </div>
                <div class="space-y-2">
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Peak Balance</span>
                        <span class="font-mono font-semibold text-slate-100 tabular-nums">${formatCurrency(currDD.peak_balance)}</span>
                    </div>
                    <div class="flex justify-between items-baseline text-sm">
                        <span class="text-slate-400">Current Balance</span>
                        <span class="font-mono font-semibold text-slate-100 tabular-nums">${formatCurrency(currDD.trough_balance)}</span>
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
    const borderClass = !isLast ? "border-b border-slate-800" : "";
    return `
        <div class="flex justify-between items-center text-sm py-2 ${borderClass}">
            <div class="flex items-center gap-3">
                <span class="text-slate-500 font-mono tabular-nums">#${index + 1}</span>
                <div>
                    <div class="font-mono font-semibold text-red-400 tabular-nums">${parseFloat(dd.drawdown_pct).toFixed(2)}%</div>
                    <div class="font-mono text-xs text-slate-400 tabular-nums">${formatCurrency(dd.drawdown_amount)}</div>
                </div>
            </div>
            <div class="text-right text-xs text-slate-400">
                <div class="font-mono tabular-nums">${dd.duration_days} day${dd.duration_days !== 1 ? "s" : ""}</div>
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
        <div>
            <h3 class="text-xs font-medium text-slate-400 uppercase tracking-wider border-b border-slate-800 pb-2 mb-3">Top Drawdown Periods (${totalPeriods} total)</h3>
            <div>
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
