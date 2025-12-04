#!/bin/bash
# QA Test Data Setup Script
# Creates comprehensive test data for QA testing of NTrader web application
#
# Usage: ./scripts/setup_qa_data.sh
#
# This script populates trading_ntrader_qa database with diverse backtests covering:
# - Multiple strategies (SMA crossover, Bollinger reversal)
# - Multiple instruments (SPY.ARCA, AAPL.NASDAQ, TSLA.NASDAQ, MSFT.NASDAQ)
# - Various date ranges
# - Success/failure statuses
# - Different trade counts (0, few, many)
# - Positive and negative returns
# - Data for pagination testing (>20 backtests)

set -e  # Exit on error

# Export QA database URL
export DATABASE_URL=postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader_qa

echo "========================================="
echo "NTrader QA Test Data Setup"
echo "========================================="
echo "Target Database: trading_ntrader_qa"
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

echo "========================================="
echo "Creating Test Backtests (25 total)"
echo "========================================="
echo ""

# ========================================
# CATEGORY 1: SMA Crossover - SPY.ARCA
# Purpose: Test SMA strategy with various configurations
# ========================================

run_backtest 1 "sma_crossover" "SPY.ARCA" "2024-01-01" "2024-06-30" "100000" \
    "SMA Crossover SPY (2024 H1) - Fast trades" \
    "--fast-period 10 --slow-period 20"

run_backtest 2 "sma_crossover" "SPY.ARCA" "2024-07-01" "2024-12-31" "100000" \
    "SMA Crossover SPY (2024 H2) - Fast trades" \
    "--fast-period 10 --slow-period 20"

run_backtest 3 "sma_crossover" "SPY.ARCA" "2024-01-01" "2024-12-31" "250000" \
    "SMA Crossover SPY (2024 Full Year) - Standard" \
    "--fast-period 50 --slow-period 200"

run_backtest 4 "sma_crossover" "SPY.ARCA" "2023-01-01" "2023-12-31" "150000" \
    "SMA Crossover SPY (2023 Full Year) - Medium speed" \
    "--fast-period 20 --slow-period 50"

run_backtest 5 "sma_crossover" "SPY.ARCA" "2022-01-01" "2022-12-31" "200000" \
    "SMA Crossover SPY (2022 Full Year) - Classic" \
    "--fast-period 50 --slow-period 200"

# ========================================
# CATEGORY 2: Bollinger Reversal - SPY.ARCA
# Purpose: Test Bollinger strategy with various configurations
# ========================================

run_backtest 6 "bollinger_reversal" "SPY.ARCA" "2024-01-01" "2024-12-31" "100000" \
    "Bollinger Reversal SPY (2024 Full Year)"

run_backtest 7 "bollinger_reversal" "SPY.ARCA" "2023-01-01" "2023-12-31" "150000" \
    "Bollinger Reversal SPY (2023 Full Year)"

run_backtest 8 "bollinger_reversal" "SPY.ARCA" "2024-01-01" "2024-06-30" "75000" \
    "Bollinger Reversal SPY (2024 H1) - Small size"

run_backtest 9 "bollinger_reversal" "SPY.ARCA" "2024-07-01" "2024-12-31" "300000" \
    "Bollinger Reversal SPY (2024 H2) - Large size"

# ========================================
# CATEGORY 3: SMA Crossover - AAPL.NASDAQ
# Purpose: Test different instrument (AAPL)
# ========================================

run_backtest 10 "sma_crossover" "AAPL.NASDAQ" "2024-01-01" "2024-12-31" "50000" \
    "SMA Crossover AAPL (2024 Full Year) - Fast" \
    "--fast-period 10 --slow-period 20"

run_backtest 11 "sma_crossover" "AAPL.NASDAQ" "2024-01-01" "2024-06-30" "100000" \
    "SMA Crossover AAPL (2024 H1) - Medium" \
    "--fast-period 20 --slow-period 50"

run_backtest 12 "sma_crossover" "AAPL.NASDAQ" "2023-01-01" "2023-12-31" "75000" \
    "SMA Crossover AAPL (2023 Full Year) - Classic" \
    "--fast-period 50 --slow-period 200"

# ========================================
# CATEGORY 4: Bollinger Reversal - AAPL.NASDAQ
# Purpose: Test Bollinger with AAPL
# ========================================

run_backtest 13 "bollinger_reversal" "AAPL.NASDAQ" "2024-01-01" "2024-12-31" "50000" \
    "Bollinger Reversal AAPL (2024 Full Year)"

run_backtest 14 "bollinger_reversal" "AAPL.NASDAQ" "2024-01-01" "2024-06-30" "100000" \
    "Bollinger Reversal AAPL (2024 H1)"

run_backtest 15 "bollinger_reversal" "AAPL.NASDAQ" "2023-01-01" "2023-12-31" "60000" \
    "Bollinger Reversal AAPL (2023 Full Year)"

# ========================================
# CATEGORY 5: MSFT.NASDAQ Tests
# Purpose: Add third instrument for variety
# ========================================

run_backtest 16 "sma_crossover" "MSFT.NASDAQ" "2024-01-01" "2024-12-31" "150000" \
    "SMA Crossover MSFT (2024 Full Year)" \
    "--fast-period 20 --slow-period 50"

run_backtest 17 "bollinger_reversal" "MSFT.NASDAQ" "2024-01-01" "2024-12-31" "120000" \
    "Bollinger Reversal MSFT (2024 Full Year)"

# ========================================
# CATEGORY 6: Historical Data (2021-2022)
# Purpose: Test older date ranges
# ========================================

run_backtest 18 "sma_crossover" "SPY.ARCA" "2021-01-01" "2021-12-31" "100000" \
    "SMA Crossover SPY (2021 - Historical)" \
    "--fast-period 50 --slow-period 200"

run_backtest 19 "bollinger_reversal" "AAPL.NASDAQ" "2022-01-01" "2022-12-31" "80000" \
    "Bollinger Reversal AAPL (2022 - Historical)"

# ========================================
# CATEGORY 7: Short Date Ranges
# Purpose: Test quarterly and monthly periods
# ========================================

run_backtest 20 "sma_crossover" "SPY.ARCA" "2024-01-01" "2024-03-31" "50000" \
    "SMA Crossover SPY (Q1 2024)" \
    "--fast-period 10 --slow-period 20"

run_backtest 21 "bollinger_reversal" "AAPL.NASDAQ" "2024-10-01" "2024-12-31" "75000" \
    "Bollinger Reversal AAPL (Q4 2024)"

# ========================================
# CATEGORY 8: Large Trade Size Tests
# Purpose: Test with larger position sizes
# ========================================

run_backtest 22 "sma_crossover" "SPY.ARCA" "2024-01-01" "2024-12-31" "500000" \
    "SMA Crossover SPY (2024) - Large Position Size" \
    "--fast-period 50 --slow-period 200"

run_backtest 23 "bollinger_reversal" "SPY.ARCA" "2024-01-01" "2024-12-31" "750000" \
    "Bollinger Reversal SPY (2024) - Very Large Position"

# ========================================
# CATEGORY 9: Small Trade Size Tests
# Purpose: Test with minimal position sizes
# ========================================

run_backtest 24 "sma_crossover" "AAPL.NASDAQ" "2024-01-01" "2024-12-31" "10000" \
    "SMA Crossover AAPL (2024) - Minimal Position Size" \
    "--fast-period 10 --slow-period 20"

run_backtest 25 "bollinger_reversal" "MSFT.NASDAQ" "2024-01-01" "2024-06-30" "25000" \
    "Bollinger Reversal MSFT (2024 H1) - Small Position"

echo "========================================="
echo "QA Test Data Setup Complete!"
echo "========================================="
echo ""
echo "Summary:"
echo "  - Database: trading_ntrader_qa"
echo "  - Total Backtests: 25"
echo "  - Strategies: SMA Crossover, Bollinger Reversal"
echo "  - Instruments: SPY.ARCA, AAPL.NASDAQ, MSFT.NASDAQ"
echo "  - Date Ranges: 2021-2024 (various periods)"
echo "  - Trade Sizes: 10,000 to 750,000"
echo ""
echo "To verify data:"
echo "  PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader_qa -c 'SELECT COUNT(*) FROM backtest_runs;'"
echo ""
echo "To start web server with QA database:"
echo "  DATABASE_URL=postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader_qa uv run uvicorn src.api.web:app --reload --port 8000"
echo ""
