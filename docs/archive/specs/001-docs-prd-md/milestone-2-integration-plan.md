# Milestone 2: Integration-First Execution Plan

**Generated**: 2025-01-14
**Philosophy**: Continuous Integration - Each task produces a working, testable system

## Integration Strategy Overview

```
PRINCIPLE: Never build in isolation. Every component integrates immediately.
APPROACH: Extend the working Milestone 1 system incrementally.
VALIDATION: After each task, the system must still run end-to-end.
```

## Current Working System (Baseline)

```bash
# What works now (Milestone 1)
uv run python -m src.cli run-simple
# ✅ Runs SMA backtest with mock data
# ✅ Displays results in Rich table
# ✅ 69 tests passing
```

## Integration Checkpoints

Each checkpoint represents a working system that can be run and tested:

```mermaid
graph LR
    M1[Milestone 1<br/>Mock Backtest] --> CP1[Checkpoint 1<br/>DB Connected]
    CP1 --> CP2[Checkpoint 2<br/>CSV Import Working]
    CP2 --> CP3[Checkpoint 3<br/>DB Data in Backtest]
    CP3 --> CP4[Checkpoint 4<br/>Full Integration]

    style M1 fill:#90EE90
    style CP1 fill:#FFE4B5
    style CP2 fill:#FFE4B5
    style CP3 fill:#FFE4B5
    style CP4 fill:#FFE4B5
```

## Detailed Integration Plan

### 🔄 Checkpoint 1: Database Connected (Tasks T013-T015)
**Goal**: System runs with database available but not yet used

#### Integration Sequence:

```python
# Task T013: Add Dependencies (30 min)
1. uv add sqlalchemy psycopg2-binary alembic
2. uv add --dev pytest-asyncio
3. ✅ Run existing tests - ALL MUST PASS
4. ✅ run-simple command still works

# Task T014: Minimal DB Config (45 min)
# src/config.py
class Settings(BaseSettings):
    # ... existing config ...
    database_url: str = "postgresql://localhost/ntrader_dev"

    @property
    def is_database_available(self) -> bool:
        """Check if database is configured"""
        return bool(self.database_url)

5. ✅ System still runs without database
6. ✅ All tests pass

# Task T015: Create Models + First Integration Test (1 hour)
# tests/integration/test_database_connection.py
@pytest.mark.integration
async def test_can_connect_to_database():
    """INTEGRATION: Verify database is accessible"""
    from src.db.session import test_connection
    assert await test_connection() is True

# src/models/market_data.py
class MarketData(BaseModel):
    # Pydantic model only - no DB operations yet
    pass

7. ✅ Integration test passes when DB running
8. ✅ Integration test skips gracefully when DB not available
9. ✅ run-simple still works (doesn't touch DB)
```

**Validation Command Sequence**:
```bash
# After EVERY task:
uv run pytest tests/                          # Unit tests still pass
uv run python -m src.cli run-simple          # Mock backtest works
uv run pytest tests/integration/ -m integration  # New integration tests pass
```

### 🔄 Checkpoint 2: CSV Import Working (Tasks T016-T019)
**Goal**: Can import CSV to database, original functionality intact

#### Integration Sequence:

```python
# Task T016: Migrations + Integration Test (45 min)
1. alembic init alembic
2. Create migration for market_data table
3. Add integration test:

# tests/integration/test_csv_import.py
@pytest.mark.integration
async def test_csv_import_stores_to_database():
    """INTEGRATION: CSV → Database flow"""
    # Create temp CSV
    # Run import
    # Query database
    # Verify data stored
    assert stored_count == expected_count

4. ✅ Test fails (no implementation yet)
5. ✅ run-simple still works

# Task T017-T018: CSV Loader with Continuous Testing (1.5 hours)
# Build incrementally:
Step 1: Parse CSV only
    - Integration test: Can read CSV
    - ✅ run-simple works

Step 2: Validate data
    - Integration test: Rejects bad data
    - ✅ run-simple works

Step 3: Store to database
    - Integration test: Data persisted
    - ✅ run-simple works

# Task T019: CLI Command Integration (1 hour)
# src/cli/main.py
@cli.group()
def data():
    """Data management commands"""
    pass

@data.command()
def import_csv(file, symbol):
    """Import CSV - NEW COMMAND"""
    # Import logic
    pass

# Keep existing command working:
@cli.command()
def run_simple():
    """Original mock backtest - UNCHANGED"""
    pass
```

**New User Capabilities**:
```bash
# Original still works:
uv run python -m src.cli run-simple

# New capability added:
uv run python -m src.cli data import-csv --file sample.csv --symbol AAPL
```

### 🔄 Checkpoint 3: Database Data in Backtest (Tasks T020-T022)
**Goal**: Backtest can use either mock OR database data

#### Integration Sequence:

```python
# Task T020: Data Service Integration (1 hour)
# src/core/backtest_runner.py
class BacktestRunner:
    def __init__(self, data_source='mock'):  # Default preserves behavior
        self.data_source = data_source

    async def run(self, strategy, symbol, start, end):
        if self.data_source == 'mock':
            data = generate_mock_data()  # Original
        else:
            data = await self.data_service.get_bars(symbol, start, end)  # New

        # Rest of backtest logic unchanged
        return self._run_backtest(data)

# Integration test:
@pytest.mark.integration
async def test_backtest_with_database_data():
    """INTEGRATION: Backtest uses real data from DB"""
    # Import sample data
    # Run backtest with data_source='database'
    # Verify results differ from mock
    pass

# Task T021: Dual-Mode Backtest Runner (1 hour)
# Refactor to support both modes cleanly
# ✅ run-simple uses mock (unchanged)
# ✅ New command uses database

# Task T022: New Backtest Command (45 min)
@cli.group()
def backtest():
    """Backtest commands"""
    pass

@backtest.command()
def run(strategy, symbol, start, end):
    """Run backtest with REAL data"""
    runner = BacktestRunner(data_source='database')
    # Run with database data
    pass

# Original command unchanged:
@cli.command()
def run_simple():
    """Run simple backtest with MOCK data"""
    runner = BacktestRunner(data_source='mock')  # Explicit now
    pass
```

**Parallel Execution Paths**:
```bash
# Path 1: Original mock flow
uv run python -m src.cli run-simple
# → BacktestRunner(mock) → generate_mock_data() → Results

# Path 2: New database flow
uv run python -m src.cli backtest run --symbol AAPL --start 2024-01-01
# → BacktestRunner(database) → DataService → PostgreSQL → Results
```

### 🔄 Checkpoint 4: Full Integration (Tasks T023-T024)
**Goal**: Complete end-to-end workflow with sample data

#### Integration Sequence:

```python
# Task T023: Sample Data + Integration Test (45 min)
1. Create data/sample_AAPL.csv with realistic data
2. Write integration test:

# tests/integration/test_end_to_end.py
@pytest.mark.integration
async def test_complete_csv_to_backtest_workflow():
    """INTEGRATION: Full workflow from CSV to results"""
    # Step 1: Import CSV
    result = await import_csv("data/sample_AAPL.csv", "AAPL")
    assert result['records_inserted'] > 0

    # Step 2: Run backtest on imported data
    backtest_result = await run_backtest("sma", "AAPL", start, end)
    assert backtest_result['total_return'] != 0

    # Step 3: Verify both modes work
    mock_result = await run_simple()
    assert mock_result is not None

    # Step 4: Results should differ
    assert backtest_result != mock_result

# Task T024: Final Integration Validation (30 min)
# Run all integration scenarios:
1. Mock-only path (original)
2. CSV import path
3. Database backtest path
4. Full end-to-end path
5. Performance benchmarks
```

## Integration Testing Matrix

| Component | Isolation Test | Integration Test | E2E Test |
|-----------|---------------|------------------|----------|
| CSV Loader | ✅ test_csv_parser | ✅ test_csv_to_db | ✅ test_full_workflow |
| Data Service | ✅ test_data_fetch | ✅ test_db_to_nautilus | ✅ test_full_workflow |
| Backtest Runner | ✅ test_mock_mode | ✅ test_db_mode | ✅ test_both_modes |
| CLI Commands | ✅ test_command_parsing | ✅ test_command_execution | ✅ test_user_workflow |

## Continuous Integration Commands

Run after EVERY task completion:

```bash
# 1. Unit tests (should always pass)
uv run pytest tests/ -v --tb=short

# 2. Integration tests (may skip if no DB)
uv run pytest tests/integration/ -m integration -v

# 3. Original functionality (must never break)
uv run python -m src.cli run-simple

# 4. New functionality (when applicable)
uv run python -m src.cli data import-csv --file data/sample.csv --symbol TEST
uv run python -m src.cli backtest run --strategy sma --symbol TEST

# 5. Coverage check
uv run pytest --cov=src --cov-report=term-missing
```

## Task Execution Order with Integration Points

```mermaid
gantt
    title Milestone 2 Integration Timeline
    dateFormat HH:mm
    section Setup
    T013 Add Dependencies       :done, 00:00, 30m
    T014 DB Config              :done, 00:30, 45m
    T015 Models + Test          :done, 01:15, 60m
    Checkpoint 1 Validation     :crit, 02:15, 15m

    section CSV Import
    T016 Migrations             :done, 02:30, 45m
    T017 CSV Test (TDD)         :done, 03:15, 30m
    T018 CSV Loader             :done, 03:45, 60m
    T019 CLI Command            :done, 04:45, 60m
    Checkpoint 2 Validation     :crit, 05:45, 15m

    section Data Integration
    T020 Data Service           :done, 06:00, 60m
    T021 Dual-Mode Runner       :done, 07:00, 60m
    T022 Backtest Command       :done, 08:00, 45m
    Checkpoint 3 Validation     :crit, 08:45, 15m

    section Final Integration
    T023 Sample Data            :done, 09:00, 45m
    T024 E2E Tests              :done, 09:45, 30m
    Checkpoint 4 Validation     :crit, 10:15, 15m
```

## Rollback Strategy

If any integration fails:

1. **Immediate**: Git stash changes, verify last working state
2. **Diagnostic**: Run integration test in isolation
3. **Fix Forward**: Small fix preferred over rollback
4. **Nuclear Option**: git reset to last checkpoint tag

```bash
# Tag each checkpoint
git tag checkpoint-1-db-connected
git tag checkpoint-2-csv-working
git tag checkpoint-3-dual-mode
git tag checkpoint-4-complete
```

## Success Metrics

### Per-Task Success Criteria
- ✅ All existing tests pass
- ✅ run-simple command works
- ✅ New integration test passes
- ✅ No performance regression

### Per-Checkpoint Success Criteria
- ✅ System fully functional
- ✅ Both old and new features work
- ✅ Can demo to stakeholder
- ✅ Could deploy if needed

### Milestone Success Criteria
- ✅ 4/4 checkpoints validated
- ✅ Can import CSV and run backtest
- ✅ Original mock functionality intact
- ✅ 80% test coverage maintained

## Anti-Patterns to Avoid

❌ **DON'T**: Build all models first, then all services, then integrate
✅ **DO**: Build model + service + integration test together

❌ **DON'T**: Break existing functionality to add new features
✅ **DO**: Extend system with new optional paths

❌ **DON'T**: Write all tests at the end
✅ **DO**: Write integration test before implementation

❌ **DON'T**: Merge without validating integration
✅ **DO**: Run full validation suite after every task

## Developer Workflow

```bash
# Start of day
git pull
uv sync
uv run pytest  # Baseline

# For each task
git checkout -b task/T0XX-description
# Write integration test FIRST
# Implement incrementally
# Run validation suite
git commit -m "feat: complete T0XX with integration"

# At checkpoint
git tag checkpoint-X-description
uv run pytest tests/integration/
# Full system demo

# End of day
git push
# Document any integration issues
```

## Risk Mitigation

### Risk: Database unavailable during development
**Mitigation**:
- Tests skip gracefully with pytest.mark.integration
- Mock mode always available as fallback
- Docker-compose for local PostgreSQL

### Risk: Breaking existing functionality
**Mitigation**:
- run-simple tested after every change
- Dual-mode architecture preserves both paths
- Integration tests catch breaks early

### Risk: Integration complexity grows
**Mitigation**:
- Small, incremental changes
- Each task fully integrated before next
- Checkpoints allow partial delivery

## Next Actions

1. **Immediate** (Before T013):
   ```bash
   # Set up PostgreSQL
   docker run -d --name ntrader-db \
     -e POSTGRES_DB=ntrader_dev \
     -e POSTGRES_USER=ntrader \
     -e POSTGRES_PASSWORD=ntrader \
     -p 5432:5432 \
     postgres:16

   # Verify connection
   psql postgresql://ntrader:ntrader@localhost/ntrader_dev
   ```

2. **Per-Task Ritual**:
   ```bash
   # Before starting task
   ./scripts/validate_integration.sh

   # After completing task
   ./scripts/validate_integration.sh
   git commit -m "feat: T0XX integrated and tested"
   ```

3. **Checkpoint Ritual**:
   ```bash
   # Full integration test suite
   uv run pytest tests/integration/ -v

   # Manual verification
   ./scripts/demo_checkpoint_X.sh

   # Tag if successful
   git tag checkpoint-X-description
   ```

---

**Key Principle**: The system is ALWAYS in a working state. Every commit represents a functional system with more capabilities than the previous commit.

**Validation**: After reading this plan, you should be able to run `uv run python -m src.cli run-simple` at ANY point during Milestone 2 development and see it work.