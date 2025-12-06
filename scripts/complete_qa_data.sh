#!/bin/bash
# Complete QA Test Data - Add Remaining Backtests (16-25)
# Adds 10 more backtests to reach 25 total for pagination testing

set -e  # Exit on error

# Use QA environment configuration
export ENV=qa

echo "========================================="
echo "Completing QA Test Data Setup"
echo "========================================="
echo "Target Database: trading_ntrader_qa"
echo "Using ENV=qa to target QA database"
echo "Adding backtests #16-25"
echo ""

# Function to run backtest
run_backtest() {
    local num=$1
    local strategy=$2
    local symbol=$3
    local start_date=$4
    local end_date=$5
    local trade_size=$6
    local description=$7
    local extra_args="${8:-}"

    echo "[$num] Creating: $description"
    echo "    Strategy: $strategy | Symbol: $symbol | Period: $start_date to $end_date"

    if [ -z "$extra_args" ]; then
        uv run python -m src.cli.main backtest run \
            -s "$strategy" \
            -sym "$symbol" \
            -st "$start_date" \
            -e "$end_date" \
            -ts "$trade_size" \
            -t 1-day
    else
        uv run python -m src.cli.main backtest run \
            -s "$strategy" \
            -sym "$symbol" \
            -st "$start_date" \
            -e "$end_date" \
            -ts "$trade_size" \
            -t 1-day \
            $extra_args
    fi

    echo "    ✓ Complete"
    echo ""
}

# ========================================
# CATEGORY 6: Additional SPY Tests
# Purpose: More date ranges and sizes
# ========================================

run_backtest 16 "sma_crossover" "SPY.ARCA" "2024-01-01" "2024-03-31" "50000" \
    "SMA Crossover SPY (Q1 2024)" \
    "--fast-period 10 --slow-period 20"

run_backtest 17 "sma_crossover" "SPY.ARCA" "2024-04-01" "2024-06-30" "75000" \
    "SMA Crossover SPY (Q2 2024)" \
    "--fast-period 20 --slow-period 50"

run_backtest 18 "sma_crossover" "SPY.ARCA" "2024-07-01" "2024-09-30" "100000" \
    "SMA Crossover SPY (Q3 2024)" \
    "--fast-period 50 --slow-period 200"

run_backtest 19 "sma_crossover" "SPY.ARCA" "2024-10-01" "2024-12-31" "150000" \
    "SMA Crossover SPY (Q4 2024)" \
    "--fast-period 10 --slow-period 20"

# ========================================
# CATEGORY 7: Additional AAPL Tests
# Purpose: More quarterly tests
# ========================================

run_backtest 20 "bollinger_reversal" "AAPL.NASDAQ" "2024-04-01" "2024-06-30" "80000" \
    "Bollinger Reversal AAPL (Q2 2024)"

run_backtest 21 "bollinger_reversal" "AAPL.NASDAQ" "2024-07-01" "2024-09-30" "90000" \
    "Bollinger Reversal AAPL (Q3 2024)"

run_backtest 22 "bollinger_reversal" "AAPL.NASDAQ" "2024-10-01" "2024-12-31" "100000" \
    "Bollinger Reversal AAPL (Q4 2024)"

# ========================================
# CATEGORY 8: Large Position Tests
# Purpose: Test larger trade sizes
# ========================================

run_backtest 23 "sma_crossover" "SPY.ARCA" "2024-01-01" "2024-12-31" "500000" \
    "SMA Crossover SPY (2024) - Large Position" \
    "--fast-period 50 --slow-period 200"

run_backtest 24 "bollinger_reversal" "SPY.ARCA" "2024-01-01" "2024-12-31" "750000" \
    "Bollinger Reversal SPY (2024) - Very Large Position"

# ========================================
# CATEGORY 9: Small Position Test
# Purpose: Test minimal position size
# ========================================

run_backtest 25 "sma_crossover" "AAPL.NASDAQ" "2024-01-01" "2024-12-31" "10000" \
    "SMA Crossover AAPL (2024) - Minimal Position" \
    "--fast-period 10 --slow-period 20"

echo "========================================="
echo "QA Test Data Completion Successful!"
echo "========================================="
echo ""
echo "Summary:"
echo "  - Database: trading_ntrader_qa"
echo "  - Backtests Added: 10"
echo "  - Total Backtests: 25"
echo "  - Strategies: SMA Crossover, Bollinger Reversal"
echo "  - Instruments: SPY.ARCA, AAPL.NASDAQ"
echo "  - Date Ranges: 2021-2024 (various periods)"
echo "  - Trade Sizes: 10,000 to 750,000"
echo ""
echo "To verify data:"
echo "  PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader_qa -c 'SELECT COUNT(*) FROM backtest_runs;'"
echo ""
