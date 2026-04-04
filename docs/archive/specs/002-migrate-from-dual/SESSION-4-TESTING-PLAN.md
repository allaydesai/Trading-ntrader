# Session 4: Comprehensive Integration Testing Plan
**Date**: 2025-10-15
**Status**: Ready to Execute
**Objective**: Validate IBKR integration across different scenarios, date ranges, and instruments

---

## Overview

With all 10 bugs fixed and basic functionality confirmed in Session 3, Session 4 focuses on comprehensive integration testing to ensure the system works reliably across various real-world scenarios.

### Session 3 Achievements ✅
- All 10 bugs fixed
- IBKR integration 100% complete
- Cache detection working
- Performance: 33x speedup (5.0s → 0.15s)
- User Stories 1, 2, 3 all passing

### Session 4 Goals 🎯
1. Validate cache behavior with different date ranges
2. Test multiple instruments (AAPL, GOOGL, MSFT, AMD)
3. Test different timeframes (1-MINUTE vs 1-DAY)
4. Verify cache persistence across restarts
5. Test edge cases (partial overlaps, gaps, boundaries)
6. Update documentation with findings

---

## Current Catalog State

Based on `find` output, we have:

### Instruments Available
- **AAPL.NASDAQ**: 1-MINUTE (2023-12-29 20:01-21:00), 1-DAY (multiple ranges)
- **GOOGL.NASDAQ**: 1-DAY (2024-01-19 to 2024-02-28)
- **MSFT.NASDAQ**: 1-DAY (2024-01-19 to 2024-02-28)
- **AMD.NASDAQ**: 1-DAY (2023-12-29 to 2024-12-31)

### Data Summary
```
Total Parquet files: 7
Instruments: 4 (AAPL, GOOGL, MSFT, AMD)
Timeframes: 2 (1-MINUTE, 1-DAY)
Date ranges: Various (2023-12-15 to 2024-12-31)
```

---

## Test Plan Structure

Tests are organized into 5 categories:
1. **Cache Hit Tests** - Data already in catalog
2. **Cache Miss Tests** - Data not in catalog (requires IBKR fetch)
3. **Multi-Instrument Tests** - Different symbols
4. **Cache Persistence Tests** - Service restart scenarios
5. **Edge Case Tests** - Boundaries, overlaps, gaps

---

## Test Category 1: Cache Hit Tests 🎯

**Goal**: Verify cache detection and fast loading with existing data

### Test 1.1: AAPL 1-MINUTE Data (Known Good)
**Status**: Known to work from Session 3
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2023-12-29 20:01:00" \
  --end "2023-12-29 21:00:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ✅ Cache hit: "Loading from catalog"
- ✅ No IBKR connection attempted
- ✅ Data loaded in ~0.15s
- ✅ 60 bars loaded
- ✅ Backtest completes successfully

**Validation**:
- Check log for "availability_cached" message
- Verify no "Connecting to IBKR" messages
- Execution time < 0.5s for data load

---

### Test 1.2: AAPL 1-DAY Data (Untested)
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2023-12-29" \
  --end "2024-01-09" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ✅ Cache hit for 1-DAY data
- ✅ Different date range than 1-MINUTE test
- ✅ Backtest completes with daily bars

**Known Issue**: Session 3 mentioned "minor issue with nanoseconds" for DAY-level data
- May need to handle `999999999Z` timestamp format
- Test will reveal if this blocks execution

---

### Test 1.3: GOOGL 1-DAY Data
```bash
uv run python -m src.cli.main backtest run \
  --strategy rsi_mean_reversion \
  --symbol GOOGL \
  --start "2024-01-19" \
  --end "2024-02-28" \
  --fast-period 14 \
  --slow-period 28
```

**Expected Results**:
- ✅ Cache hit for GOOGL data
- ✅ Different strategy (RSI Mean Reversion)
- ✅ Longer date range (~40 days)
- ✅ Backtest completes

---

### Test 1.4: AMD 1-DAY Data (Long Range)
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_momentum \
  --symbol AMD \
  --start "2023-12-29" \
  --end "2024-12-31" \
  --fast-period 50 \
  --slow-period 200
```

**Expected Results**:
- ✅ Cache hit for AMD data
- ✅ Very long range (~1 year)
- ✅ Tests catalog handling of large datasets
- ✅ Backtest completes

**Performance Expectation**: Even with 1 year of daily data (~252 bars), load should be fast (<1s)

---

## Test Category 2: Cache Miss Tests 📡

**Goal**: Verify automatic IBKR fetching when data not in catalog

**Prerequisites**:
- IBKR TWS/Gateway must be running
- `export IBKR_PORT=7497` (or your TWS port)
- Market data subscription active

---

### Test 2.1: New Symbol (TSLA)
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol TSLA \
  --start "2024-01-02 09:30:00" \
  --end "2024-01-02 10:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ⚠️ Cache miss detected
- ✅ "No data in catalog for TSLA" message
- ✅ "Will attempt to fetch from IBKR..." message
- ✅ IBKR connection successful
- ✅ Instrument lookup: TSLA.NASDAQ resolved
- ✅ Fetched ~60 bars in ~2-5s
- ✅ Data saved to catalog
- ✅ "Future backtests will use cached data" message
- ✅ Backtest completes

**Validation**:
- Check new Parquet file created: `data/catalog/data/bar/TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL/*.parquet`
- Verify file size > 0
- Re-run same command, should hit cache second time

---

### Test 2.2: Date Range Outside Catalog (AAPL)
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2024-06-01 09:30:00" \
  --end "2024-06-01 10:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ⚠️ Cache miss (date range not in catalog)
- ✅ IBKR fetch initiated
- ✅ New date range saved to catalog
- ✅ Backtest completes

**Edge Case**: Catalog has AAPL data but for different dates
- Tests cache's ability to detect date range gaps

---

### Test 2.3: Partial Overlap (AAPL Extended)
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2023-12-29 19:30:00" \
  --end "2023-12-29 21:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ⚠️ Partial overlap detected (catalog has 20:01-21:00, need 19:30-21:30)
- 🤔 **Question**: Will system fetch only missing data or entire range?
- Current implementation may fetch entire range
- Future optimization: Fetch only gaps

**This test reveals current behavior for partial overlaps**

---

## Test Category 3: Multi-Instrument Tests 🎸

**Goal**: Verify system handles multiple instruments in sequence

### Test 3.1: Sequential Runs (Different Instruments)
Run backtests for multiple instruments one after another:

```bash
# AAPL
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover --symbol AAPL \
  --start "2023-12-29 20:01:00" --end "2023-12-29 21:00:00" \
  --fast-period 5 --slow-period 10

# GOOGL
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover --symbol GOOGL \
  --start "2024-01-19" --end "2024-01-26" \
  --fast-period 5 --slow-period 10

# MSFT
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover --symbol MSFT \
  --start "2024-01-19" --end "2024-01-26" \
  --fast-period 5 --slow-period 10
```

**Expected Results**:
- ✅ All three backtests succeed
- ✅ Each uses its respective cache entry
- ✅ No cache contamination between instruments
- ✅ Consistent performance (~0.15s per cached load)

---

### Test 3.2: Cache Detection for Multiple Instruments
Verify cache shows all instruments:

```bash
uv run python -c "
from src.services.data_catalog import DataCatalogService
service = DataCatalogService()
print(f'Cache entries: {len(service.availability_cache)}')
print()
for key, avail in sorted(service.availability_cache.items()):
    print(f'{key}:')
    print(f'  Start: {avail.start_date}')
    print(f'  End: {avail.end_date}')
    print(f'  Files: {avail.file_count}, Rows: ~{avail.estimated_row_count}')
    print()
"
```

**Expected Results**:
- ✅ Shows all instruments in catalog (AAPL, GOOGL, MSFT, AMD)
- ✅ Correct date ranges for each
- ✅ File counts match actual Parquet files

---

## Test Category 4: Cache Persistence Tests 🔄

**Goal**: Verify cache rebuilds correctly after service restart

### Test 4.1: Cache Rebuild After Service Restart

**Step 1**: Note current cache state
```bash
uv run python -c "
from src.services.data_catalog import DataCatalogService
service = DataCatalogService()
print(f'Cache entries BEFORE: {len(service.availability_cache)}')
"
```

**Step 2**: Simulate service restart (new Python process)
```bash
uv run python -c "
from src.services.data_catalog import DataCatalogService
service = DataCatalogService()
print(f'Cache entries AFTER: {len(service.availability_cache)}')
"
```

**Expected Results**:
- ✅ Cache entry count matches before/after
- ✅ Cache rebuilds from disk on initialization
- ✅ No data loss after restart

---

### Test 4.2: Backtest After Restart
Run a backtest immediately after simulated restart:

```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2023-12-29 20:01:00" \
  --end "2023-12-29 21:00:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ✅ Cache hit (not cache miss)
- ✅ Data loaded from catalog
- ✅ No IBKR fetch attempted
- ✅ Performance same as Session 3 (~0.15s)

---

## Test Category 5: Edge Case Tests ⚠️

**Goal**: Test boundary conditions and error scenarios

### Test 5.1: Future Date (Should Fail)
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2025-12-01 09:30:00" \
  --end "2025-12-01 10:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ❌ IBKR fetch fails (future date)
- ✅ Clear error message: "Historical data not available for future dates"
- ✅ User Story 3 validation (clear error messages)

---

### Test 5.2: Market Closed (Weekend/Holiday)
```bash
# Test on a Saturday
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2024-01-06 09:30:00" \
  --end "2024-01-06 10:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ⚠️ IBKR fetch may return no data (market closed)
- ✅ Error handled gracefully
- ✅ Clear message: "No market data available for this date (market may be closed)"

---

### Test 5.3: Invalid Symbol
```bash
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol INVALID123 \
  --start "2024-01-02 09:30:00" \
  --end "2024-01-02 10:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ❌ IBKR instrument lookup fails
- ✅ Clear error message: "Symbol INVALID123 not found"
- ✅ Suggestion: "Check symbol spelling or market"

---

### Test 5.4: IBKR Disconnected
```bash
# Stop IBKR TWS/Gateway first
docker compose stop ibgateway  # or close TWS

# Then try backtest with missing data
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol NVDA \
  --start "2024-01-02 09:30:00" \
  --end "2024-01-02 10:30:00" \
  --fast-period 5 \
  --slow-period 10
```

**Expected Results**:
- ❌ IBKR connection fails
- ✅ Clear error message with recovery steps
- ✅ Suggestion: "docker compose up ibgateway" or "Start TWS"
- ✅ Exit code: 3 (connection error)

---

## Test Execution Order

### Recommended Sequence

**Phase 1: Quick Validation** (~5 minutes)
1. Test 1.1 - AAPL 1-MINUTE (confirm Session 3 still works)
2. Test 3.2 - Cache detection (verify cache state)

**Phase 2: Cache Hit Scenarios** (~10 minutes)
3. Test 1.2 - AAPL 1-DAY
4. Test 1.3 - GOOGL 1-DAY
5. Test 1.4 - AMD 1-DAY (long range)

**Phase 3: Cache Miss Scenarios** (~15 minutes, requires IBKR)
6. Test 2.1 - New symbol (TSLA)
7. Test 2.2 - New date range (AAPL future date)
8. Test 2.3 - Partial overlap

**Phase 4: Persistence** (~5 minutes)
9. Test 4.1 - Cache rebuild
10. Test 4.2 - Backtest after restart

**Phase 5: Edge Cases** (~10 minutes)
11. Test 5.1 - Future date
12. Test 5.2 - Market closed
13. Test 5.3 - Invalid symbol
14. Test 5.4 - IBKR disconnected

**Total Estimated Time**: ~45 minutes

---

## Success Criteria

### Must Pass ✅
- All cache hit tests succeed (Tests 1.1-1.4)
- New symbol fetch works (Test 2.1)
- Cache persistence works (Tests 4.1-4.2)
- Clear error messages for failures (Tests 5.1-5.4)

### Should Pass ⚠️
- Multiple instruments work (Test 3.1)
- Partial overlap handled gracefully (Test 2.3)
- Performance consistent (~0.15s cached, ~5s IBKR)

### Nice to Have 💡
- Future date handled with clear message (Test 5.1)
- Market closed detected (Test 5.2)
- Invalid symbols rejected clearly (Test 5.3)

---

## Data Collection

### For Each Test, Record:
1. **Test ID** (e.g., 1.1, 2.1)
2. **Status** (✅ Pass, ❌ Fail, ⚠️ Partial)
3. **Execution Time**
4. **Data Source** (Cache or IBKR)
5. **Error Messages** (if any)
6. **Observations**

### Sample Format:
```
Test 1.1: AAPL 1-MINUTE Cache Hit
Status: ✅ Pass
Execution Time: 0.14s
Data Source: Cache
Bars Loaded: 60
Observations: Same performance as Session 3
```

---

## Documentation Updates

After testing, update these files:

1. **TESTING-SUMMARY.md** - Add Session 4 results
2. **SESSION-4-SUMMARY.md** - Create new summary
3. **tasks.md** - Update progress if needed
4. **README.md** - Add cache usage examples
5. **quickstart.md** - Update with cache workflow

---

## Known Issues to Watch

From Session 3 summary:

1. **DAY-Level Data**:
   - Minor issue with nanoseconds (`999999999Z`)
   - May cause parsing errors
   - Test 1.2 will reveal impact

2. **Partial Overlaps**:
   - Current implementation may not fetch only gaps
   - Test 2.3 will show actual behavior
   - Future optimization opportunity

3. **Rate Limiting**:
   - Configured for 45 req/sec
   - Should not trigger during these tests (too few requests)
   - Can be tested with bulk data fetch if needed

---

## Emergency Rollback

If tests reveal critical issues:

1. **Identify the commit** causing problems
2. **Review** SESSION-3-SUMMARY.md for working state
3. **Rollback** if needed:
   ```bash
   git log --oneline -5
   git revert <commit-hash>
   ```

Last known good commit: `473aa7c`

---

## Next Steps After Session 4

Depending on results:

### If All Tests Pass ✅
1. Update README with comprehensive examples
2. Create troubleshooting guide
3. Consider User Story 4 (CSV import to Parquet)
4. Consider User Story 5 (Data inspection commands)

### If Issues Found ⚠️
1. Document new bugs in bug-report.md
2. Prioritize by severity
3. Plan Session 5 for fixes
4. Update tasks.md with blockers

### If Performance Issues 🐌
1. Profile slow operations
2. Optimize cache rebuild logic
3. Consider file-level metadata caching
4. Add LRU eviction if memory usage high

---

## Resources

### Useful Commands

**Check Cache State**:
```bash
uv run python -c "
from src.services.data_catalog import DataCatalogService
service = DataCatalogService()
for key, avail in sorted(service.availability_cache.items()):
    print(f'{key}: {avail.start_date} to {avail.end_date} ({avail.file_count} files)')
"
```

**List Catalog Files**:
```bash
find data/catalog -type f -name "*.parquet" | sort
```

**Check IBKR Connection**:
```bash
export IBKR_PORT=7497
uv run python -m src.cli.main data connect
```

**Clear Cache (Nuclear Option)**:
```bash
# DANGER: Removes all cached data
rm -rf data/catalog/data/bar/*
```

---

## Conclusion

This comprehensive test plan covers:
- ✅ Happy path scenarios (cache hits)
- ⚠️ Failure scenarios (cache misses, IBKR fetch)
- 🔄 Persistence scenarios (restarts)
- ⚠️ Edge cases (errors, boundaries)

**Estimated Duration**: 45 minutes
**Prerequisites**: IBKR TWS/Gateway running
**Expected Outcome**: Full validation of IBKR integration

Ready to execute! 🚀

---

*Created: 2025-10-15*
*Status: Ready for Session 4*
*Previous: SESSION-3-SUMMARY.md*
