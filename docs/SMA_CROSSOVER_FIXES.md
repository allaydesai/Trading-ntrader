# SMA Crossover Strategy - Issues & Fix Plan

**Date**: 2025-11-01
**Status**: ✅ FIXED - Percentage-based position sizing implemented
**Priority**: COMPLETED

---

## Implementation Summary (2025-11-01)

### What Was Implemented

**Percentage-Based Position Sizing**: The SMA Crossover strategy now uses percentage-based position sizing instead of fixed share counts.

#### Key Changes:

1. **New Configuration Fields** (`src/config.py:106-113`):
   - `portfolio_value`: Starting portfolio value in USD (default: $1,000,000)
   - `position_size_pct`: Position size as percentage of portfolio (default: 10%)

2. **Updated SMAConfig** (`src/core/strategies/sma_crossover.py:16-42`):
   - Removed fixed `trade_size` parameter
   - Added `portfolio_value` and `position_size_pct` parameters
   - Added comprehensive docstring with examples

3. **Dynamic Position Sizing** (`src/core/strategies/sma_crossover.py:119-161`):
   - New `_calculate_position_size()` method
   - Calculates shares based on: `(portfolio_value * position_size_pct / 100) / current_price`
   - Ensures minimum of 1 share per trade
   - Logs position sizing details for transparency

4. **Improved Position Tracking** (`src/core/strategies/sma_crossover.py:184-252`):
   - Removed manual position tracking (unreliable)
   - Uses Nautilus cache queries for real-time position state
   - Prevents duplicate positions and improves reliability

5. **Updated All Backtest Methods**:
   - `run_sma_backtest()`: Added portfolio parameters
   - `run_backtest_with_database()`: Added portfolio parameters
   - All strategy creation points updated to pass new parameters

### Example Usage

```python
# With default 10% position sizing
# Portfolio: $1,000,000
# Position %: 10%
# Stock Price: $140
# Shares = ($1M * 10%) / $140 = 714 shares (~$100K notional)

# Configuration:
config = SMAConfig(
    instrument_id=instrument_id,
    bar_type=bar_type,
    fast_period=10,
    slow_period=20,
    portfolio_value=Decimal("1000000"),  # $1M portfolio
    position_size_pct=Decimal("10.0"),    # 10% per trade
)
```

### Benefits

1. **Risk-Based Position Sizing**: Trade size automatically scales with portfolio value
2. **Consistent Risk**: Each position represents the same percentage of capital
3. **No More Capital Rejections**: Position sizes are always appropriate for available capital
4. **Price-Adaptive**: Share count adjusts based on current stock price
5. **Transparent Logging**: Every position calculation is logged with details

### Migration Notes

- Old `trade_size` parameter is deprecated but still exists in config for backwards compatibility
- All new backtests should use `portfolio_value` and `position_size_pct`
- Default 10% position sizing means 10 positions can be held if fully invested

---

## Original Issue Analysis (Historical Context)

## Executive Summary

The SMA Crossover strategy implementation has **critical configuration issues** that prevent it from executing trades. The core crossover logic is **correct**, but the trade size parameter is configured incorrectly, making positions 140x larger than available capital.

### Impact Assessment

- **Severity**: 🔴 CRITICAL
- **User Impact**: Backtests appear to run but generate no trades
- **Root Cause**: Trade size misconfiguration (1M shares vs reasonable amount)
- **Fix Complexity**: LOW (configuration changes only)
- **Estimated Time**: 30 minutes

---

## Issues Identified

### Issue 1: ❌ CRITICAL - Trade Size in Shares Exceeds Capital

**File**: `src/core/strategies/sma_crossover.py`
**Lines**: 23, 41
**Severity**: 🔴 CRITICAL - Prevents all trade execution

#### Current Code
```python
class SMAConfig(StrategyConfig):
    """Configuration for SMA crossover strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    fast_period: int = 10
    slow_period: int = 20
    trade_size: Decimal = Decimal("1_000_000")  # ← 1 MILLION SHARES!
```

#### Problem Analysis

The `trade_size` parameter represents **number of shares**, not notional value:

- **Default**: 1,000,000 shares
- **Example (GOOG @ $140/share)**:
  - Notional value = 1,000,000 × $140 = **$140,000,000** (140 million dollars)
  - Starting capital = $1,000,000 (1 million dollars)
  - **Ratio**: 140:1 (position size is 140x larger than capital!)

#### Why Trades Fail

```
Order Rejection Flow:
1. Strategy generates buy signal
2. Creates market order for 1,000,000 shares
3. Nautilus risk checks calculate required capital: $140M
4. Available capital: $1M
5. Order REJECTED - Insufficient funds
6. No position opened
7. No trades recorded in backtest results
```

#### Evidence from User's Backtest

Looking at the user's earlier backtest command:
```bash
bt backtest run -s sma_crossover -sym GOOG -st 2023-01-01 -e 2024-01-01 -t 1-day
Trade Size: 1,000,000
```

The output shows:
- ✅ Data loaded successfully
- ✅ Strategy initialized
- ✅ Backtest completed
- ❌ **Likely zero trades executed** (due to insufficient capital)

---

### Issue 2: ⚠️ MEDIUM - Manual Position Tracking is Risky

**File**: `src/core/strategies/sma_crossover.py`
**Lines**: 56, 96-100, 126-147
**Severity**: ⚠️ MEDIUM - May cause duplicate positions or missed trades

#### Current Code
```python
class SMACrossover(Strategy):
    def __init__(self, config: SMAConfig) -> None:
        # ...
        self.position: Position | None = None  # ← Manual tracking

    def on_event(self, event: Event) -> None:
        """Handle events."""
        # Track position changes
        if hasattr(event, "position_id"):
            self.position = self.cache.position(event.position_id)  # ← Unreliable

    def _generate_buy_signal(self) -> None:
        """Generate a buy signal."""
        if self.position and self.position.is_short:  # ← Using manual tracking
            self.close_position(self.position)

        if not self.position or not self.position.is_long:  # ← May be stale
            order = self.order_factory.market(...)
```

#### Problem Analysis

**Risks with Manual Tracking**:
1. **Event Dependency**: Relies on events having `position_id` attribute
2. **Cache Staleness**: `self.position` may not reflect current state
3. **Race Conditions**: Events may arrive out of order
4. **Multiple Positions**: Doesn't handle multiple positions per instrument

**Why This Matters**:
- If `self.position` is `None` but a position actually exists → Duplicate position attempted
- If `self.position` is stale → Wrong position closed
- If events are missed → Position tracking breaks

#### Nautilus Best Practice

Nautilus Trader provides built-in position queries that are always up-to-date:

```python
# Get all positions for this instrument
positions = self.cache.positions(
    venue=self.instrument_id.venue,
    instrument_id=self.instrument_id
)

# Check for open positions
has_long = any(p.is_long and p.is_open for p in positions)
has_short = any(p.is_short and p.is_open for p in positions)
```

---

### Issue 3: 📝 LOW - Redundant Null Check

**File**: `src/core/strategies/sma_crossover.py`
**Lines**: 89-90, 113-114
**Severity**: 📝 LOW - Code quality issue, no functional impact

#### Current Code
```python
def on_bar(self, bar: Bar) -> None:
    # ...
    # Check for crossover signals only if we have previous values
    if self._prev_fast_sma is not None and self._prev_slow_sma is not None:
        self._check_for_signals(fast_value, slow_value)  # ← First check

    # Store current values for next iteration
    self._prev_fast_sma = fast_value
    self._prev_slow_sma = slow_value

def _check_for_signals(self, fast_value: float, slow_value: float) -> None:
    # Only check for crossovers if we have previous values
    if self._prev_fast_sma is not None and self._prev_slow_sma is not None:  # ← DUPLICATE
        # Detect crossovers...
```

#### Problem
The condition is checked twice - redundant but harmless.

---

### Issue 4: 📋 DOCUMENTATION - Missing Trade Size Units

**File**: `CLAUDE.md`, `README.md`, CLI help text
**Severity**: 📋 DOCUMENTATION - User confusion

#### Current State
```python
# From CLAUDE.md
trade_size: Decimal = Field(
    default=Decimal("1000000"),
    description="Default trade size in SHARES (not USD notional)",  # ← Good!
)
```

**Good**: The `CLAUDE.md` was recently updated to clarify this.

**Missing**: CLI help text and strategy docstrings don't mention units:
```bash
bt backtest run --help
# Should show: --trade-size: Number of SHARES to trade (not USD notional)
```

---

## Crossover Logic ✅ VERIFIED CORRECT

The actual SMA crossover detection logic is **CORRECT** and well-implemented:

### Bullish Crossover (Golden Cross)
```python
# Detect bullish crossover (fast SMA crosses above slow SMA)
if self._prev_fast_sma <= self._prev_slow_sma and fast_value > slow_value:
    self._generate_buy_signal()
```

**Logic**:
- Previous state: Fast SMA ≤ Slow SMA
- Current state: Fast SMA > Slow SMA
- **Action**: BUY signal ✅

### Bearish Crossover (Death Cross)
```python
# Detect bearish crossover (fast SMA crosses below slow SMA)
elif self._prev_fast_sma >= self._prev_slow_sma and fast_value < slow_value:
    self._generate_sell_signal()
```

**Logic**:
- Previous state: Fast SMA ≥ Slow SMA
- Current state: Fast SMA < Slow SMA
- **Action**: SELL signal ✅

### Why This is Correct

1. **Proper Crossover Detection**: Checks both previous and current states
2. **No False Signals**: Won't trigger if SMAs are already in the target state
3. **Clean State Tracking**: Updates `_prev_fast_sma` and `_prev_slow_sma` after each bar
4. **Initialization Handling**: Waits for both indicators to initialize before trading

---

## Fix Plan

### Phase 1: Critical Fix - Trade Size (REQUIRED) ⚡

**Priority**: 🔴 CRITICAL
**Time**: 10 minutes
**Risk**: LOW (configuration only)

#### Changes Required

**File**: `src/core/strategies/sma_crossover.py:23`

```python
# BEFORE:
trade_size: Decimal = Decimal("1_000_000")  # 1M shares = $140M @ GOOG

# AFTER (Option 1 - Conservative):
trade_size: Decimal = Decimal("100")  # 100 shares ≈ $14K @ $140/share

# AFTER (Option 2 - Moderate):
trade_size: Decimal = Decimal("500")  # 500 shares ≈ $70K @ $140/share

# AFTER (Option 3 - Aggressive):
trade_size: Decimal = Decimal("1000")  # 1000 shares ≈ $140K @ $140/share
```

**Recommendation**: Use **Option 2 (500 shares)** for balanced risk/reward.

**Calculation for $100K Position**:
```python
# For GOOG @ $140/share, to get ~$100K position:
target_notional = 100_000  # $100K
price_per_share = 140  # GOOG price
shares_needed = target_notional / price_per_share ≈ 714 shares

# Round to:
trade_size: Decimal = Decimal("700")  # ~$98K position
```

---

### Phase 2: Improve Position Tracking (RECOMMENDED) 🛡️

**Priority**: ⚠️ MEDIUM
**Time**: 15 minutes
**Risk**: LOW (defensive improvement)

#### Changes Required

**File**: `src/core/strategies/sma_crossover.py`

**Remove Manual Tracking** (lines 56, 96-100):
```python
# DELETE:
self.position: Position | None = None  # Line 56

# DELETE:
def on_event(self, event: Event) -> None:  # Lines 96-100
    """Handle events."""
    if hasattr(event, "position_id"):
        self.position = self.cache.position(event.position_id)
```

**Update Signal Methods** (lines 123-161):
```python
def _generate_buy_signal(self) -> None:
    """Generate a buy signal."""
    # Get current positions from cache (always up-to-date)
    positions = self.cache.positions(
        venue=self.instrument_id.venue,
        instrument_id=self.instrument_id
    )

    # Check for open positions
    has_short = any(p.is_short and p.is_open for p in positions)
    has_long = any(p.is_long and p.is_open for p in positions)

    # Close any existing short position first
    if has_short:
        for position in positions:
            if position.is_short and position.is_open:
                self.close_position(position)
                self.log.info(f"Closed SHORT position: {position.id}")

    # Only open new long position if we don't already have one
    if not has_long:
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.trade_size,
        )
        self.submit_order(order)

        self.log.info(
            f"Generated BUY signal - Fast SMA: {self.fast_sma.value:.4f}, "
            f"Slow SMA: {self.slow_sma.value:.4f}"
        )

def _generate_sell_signal(self) -> None:
    """Generate a sell signal."""
    # Get current positions from cache
    positions = self.cache.positions(
        venue=self.instrument_id.venue,
        instrument_id=self.instrument_id
    )

    # Check for open positions
    has_long = any(p.is_long and p.is_open for p in positions)
    has_short = any(p.is_short and p.is_open for p in positions)

    # Close any existing long position first
    if has_long:
        for position in positions:
            if position.is_long and position.is_open:
                self.close_position(position)
                self.log.info(f"Closed LONG position: {position.id}")

    # Only open new short position if we don't already have one
    if not has_short:
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.trade_size,
        )
        self.submit_order(order)

        self.log.info(
            f"Generated SELL signal - Fast SMA: {self.fast_sma.value:.4f}, "
            f"Slow SMA: {self.slow_sma.value:.4f}"
        )
```

---

### Phase 3: Code Cleanup (OPTIONAL) 🧹

**Priority**: 📝 LOW
**Time**: 5 minutes
**Risk**: NONE (style only)

#### Remove Redundant Check

**File**: `src/core/strategies/sma_crossover.py:113-114`

```python
# BEFORE:
def _check_for_signals(self, fast_value: float, slow_value: float) -> None:
    # Only check for crossovers if we have previous values
    if self._prev_fast_sma is not None and self._prev_slow_sma is not None:  # ← DELETE THIS
        # Detect bullish crossover (fast SMA crosses above slow SMA)
        if self._prev_fast_sma <= self._prev_slow_sma and fast_value > slow_value:
            self._generate_buy_signal()

        # Detect bearish crossover (fast SMA crosses below slow SMA)
        elif self._prev_fast_sma >= self._prev_slow_sma and fast_value < slow_value:
            self._generate_sell_signal()

# AFTER:
def _check_for_signals(self, fast_value: float, slow_value: float) -> None:
    """
    Check for crossover signals and generate orders.

    Note: This method is only called after verifying previous values exist.

    Parameters
    ----------
    fast_value : float
        Current fast SMA value.
    slow_value : float
        Current slow SMA value.
    """
    # Detect bullish crossover (fast SMA crosses above slow SMA)
    if self._prev_fast_sma <= self._prev_slow_sma and fast_value > slow_value:
        self._generate_buy_signal()

    # Detect bearish crossover (fast SMA crosses below slow SMA)
    elif self._prev_fast_sma >= self._prev_slow_sma and fast_value < slow_value:
        self._generate_sell_signal()
```

---

### Phase 4: Documentation Updates (RECOMMENDED) 📚

**Priority**: 📋 MEDIUM
**Time**: 10 minutes
**Risk**: NONE

#### Update CLI Help Text

**File**: `src/cli/commands/backtest.py`

```python
@click.option(
    "--trade-size",
    "-t",
    type=str,
    default="1-day",
    help="Trade size in SHARES (not USD notional). Example: 500 shares = ~$70K @ $140/share",  # ← ADD UNITS
)
```

#### Update Strategy Docstring

**File**: `src/core/strategies/sma_crossover.py:16-24`

```python
class SMAConfig(StrategyConfig):
    """
    Configuration for SMA crossover strategy.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument to trade.
    bar_type : BarType
        The bar type for strategy execution.
    fast_period : int, default=10
        Period for fast moving average.
    slow_period : int, default=20
        Period for slow moving average.
    trade_size : Decimal, default=100
        Number of SHARES to trade per signal (not USD notional value).
        Example: 500 shares of GOOG @ $140 = $70,000 notional position.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    fast_period: int = 10
    slow_period: int = 20
    trade_size: Decimal = Decimal("100")  # Updated default
```

---

## Testing Plan

### Step 1: Unit Tests for Trade Size

**File**: `tests/unit/strategies/test_sma_crossover.py` (create if needed)

```python
def test_sma_config_trade_size_default():
    """Test that default trade size is reasonable for $1M capital."""
    # Given: Default config
    # When: Creating config without trade_size
    # Then: Default should be 100 shares (not 1M)

    # Implementation would verify Decimal("100") is default

def test_sma_trade_size_converts_to_quantity():
    """Test that trade_size properly converts to Nautilus Quantity."""
    # Given: Config with trade_size = 500
    # When: Strategy initializes
    # Then: self.trade_size should be Quantity of 500

    # Implementation would verify conversion

def test_position_notional_within_capital():
    """Test that trade size doesn't exceed available capital."""
    # Given: $1M capital, GOOG @ $140/share
    # When: Trade size = 500 shares
    # Then: Notional = $70K < $1M (pass)

    # Implementation would verify position sizing
```

### Step 2: Integration Test with Real Data

**Test**: Run backtest with fixed trade size on actual GOOG data

```bash
# Test with new default (100 shares)
bt backtest run -s sma_crossover -sym GOOG -st 2023-01-01 -e 2024-01-01

# Expected results:
# - Trades should execute (not rejected)
# - Position sizes: ~$14K notional (100 shares × $140)
# - Multiple trades recorded
# - Final balance different from initial
```

### Step 3: Position Tracking Verification

**File**: Add logging to verify position tracking works

```python
def _generate_buy_signal(self) -> None:
    """Generate a buy signal."""
    positions = self.cache.positions(
        venue=self.instrument_id.venue,
        instrument_id=self.instrument_id
    )

    # Log position state for verification
    self.log.info(
        f"Position check - Total: {len(positions)}, "
        f"Open: {sum(1 for p in positions if p.is_open)}, "
        f"Long: {sum(1 for p in positions if p.is_long and p.is_open)}, "
        f"Short: {sum(1 for p in positions if p.is_short and p.is_open)}"
    )
```

---

## Implementation Checklist

### Phase 1: Critical Fix ⚡ (REQUIRED)
- [ ] Update `sma_crossover.py:23` - Change default trade_size to `Decimal("100")`
- [ ] Run `ruff format src/core/strategies/sma_crossover.py`
- [ ] Run `ruff check src/core/strategies/sma_crossover.py`
- [ ] Test backtest with GOOG data
- [ ] Verify trades execute (not rejected)
- [ ] Commit changes

### Phase 2: Position Tracking 🛡️ (RECOMMENDED)
- [ ] Remove `self.position` attribute (line 56)
- [ ] Remove `on_event()` method (lines 96-100)
- [ ] Update `_generate_buy_signal()` to use cache queries
- [ ] Update `_generate_sell_signal()` to use cache queries
- [ ] Add position state logging
- [ ] Run `ruff format` and `ruff check`
- [ ] Test backtest verifies no duplicate positions
- [ ] Commit changes

### Phase 3: Code Cleanup 🧹 (OPTIONAL)
- [ ] Remove redundant null check (line 113-114)
- [ ] Update `_check_for_signals()` docstring
- [ ] Run `ruff format` and `ruff check`
- [ ] Commit changes

### Phase 4: Documentation 📚 (RECOMMENDED)
- [ ] Update `SMAConfig` docstring with trade_size units
- [ ] Update CLI help text for `--trade-size`
- [ ] Update `README.md` with trade size examples
- [ ] Commit changes

---

## Risk Assessment

### Low Risk Changes ✅
- **Trade size update**: Configuration only, easily reversible
- **Code cleanup**: Style improvements, no logic changes
- **Documentation**: No code changes

### Medium Risk Changes ⚠️
- **Position tracking**: Logic change, but defensive improvement
  - **Mitigation**: Existing cache queries are well-tested in Nautilus
  - **Fallback**: Can revert if issues arise
  - **Testing**: Integration tests will catch problems

### No Breaking Changes ✅
- All changes are backward compatible
- Existing backtests will work (just with corrected trade size)
- No API changes required

---

## Expected Outcomes

### Before Fixes
```
Backtest Results:
- Total Trades: 0 (all rejected due to insufficient capital)
- Total Return: $0
- Final Balance: $1,000,000 (unchanged)
- Win Rate: N/A
```

### After Phase 1 (Trade Size Fix)
```
Backtest Results:
- Total Trades: 15-30 (realistic number based on crossovers)
- Position Size: ~$14K per trade (100 shares × $140)
- Total Return: +/- $X (actual strategy performance)
- Final Balance: $1,000,000 +/- $X
- Win Rate: 40-60% (typical for MA crossover)
```

### After Phase 2 (Position Tracking)
```
Additional Benefits:
- More reliable position management
- Better handling of edge cases
- Clearer logs for debugging
- Future-proof for multiple positions
```

---

## Monitoring & Validation

### Key Metrics to Track

1. **Trade Execution Rate**
   - Before: 0% (all rejected)
   - After: >90% (most signals execute)

2. **Position Size**
   - Before: Attempted $140M (rejected)
   - After: ~$14K-$140K (based on chosen trade_size)

3. **Capital Utilization**
   - Before: 0% (no positions)
   - After: 1-14% per position (healthy range)

4. **Number of Trades**
   - Before: 0
   - After: 15-30 per year (depends on crossover frequency)

### Validation Steps

```bash
# 1. Run backtest with verbose logging
bt backtest run -s sma_crossover -sym GOOG -st 2023-01-01 -e 2024-01-01 --verbose

# 2. Check for successful trade execution
grep "Generated BUY signal" logs/*.log
grep "Generated SELL signal" logs/*.log

# 3. Verify no capital rejections
grep -i "insufficient" logs/*.log  # Should be empty

# 4. Check final results
bt history --latest
```

---

## References

### Related Files
- `src/core/strategies/sma_crossover.py` - Main strategy implementation
- `src/core/backtest_runner.py` - Backtest execution (lines 1069-1078)
- `src/cli/commands/backtest.py` - CLI interface (lines 376-384)
- `CLAUDE.md` - Project documentation (recently updated with trade size note)

### Nautilus Trader Documentation
- [Position Management](https://nautilustrader.io/docs/concepts/positions)
- [Strategy API](https://nautilustrader.io/docs/api_reference/trading#strategy)
- [Cache Queries](https://nautilustrader.io/docs/api_reference/cache)

### Related Issues
- IBKR connection fix (completed 2025-11-01)
- Database migration fix (completed 2025-11-01)
- Results tuple unpacking fix (completed 2025-11-01)

---

## Sign-off

**Created by**: Claude Code Analysis
**Date**: 2025-11-01
**Review Status**: Pending user approval
**Priority**: 🔴 HIGH - Blocking production use

**Recommended Action**: Proceed with Phase 1 (Critical Fix) immediately. Phase 2 (Position Tracking) can be done as a follow-up improvement.
