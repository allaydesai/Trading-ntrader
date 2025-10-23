## Popular Testing Patterns for C Extensions and Complex Dependencies

### 1. **Process Isolation Pattern (NumPy, pandas, scikit-learn approach)**

The most widely adopted pattern for testing C extensions that might crash is process isolation using pytest-forked or pytest-xdist's boxing feature, which runs each test in a forked subprocess to survive SEGFAULTS or dying processes:

```python
# pytest.ini
[tool:pytest]
addopts = 
    --forked  # Run each test in isolated subprocess
    -n auto   # Parallel execution with auto CPU detection
    --max-worker-restart=3  # Restart workers after crashes

# For specific tests that need isolation
import pytest

@pytest.mark.forked  # Force this test to run in subprocess
def test_risky_c_extension():
    """Test that might crash the interpreter"""
    pass

# Combine with xdist for parallel + isolated execution
# pytest --forked -n 4  # 4 parallel workers, each test isolated
```

### 2. **Subprocess Test Runner Pattern (pytest-isolate approach)**

A modern approach using pytest-isolate provides fine-grained control over subprocess isolation with resource limits:

```python
import pytest
import subprocess
import sys

class IsolatedTestRunner:
    """Run tests in complete isolation with resource limits"""
    
    @staticmethod
    def run_in_subprocess(test_code, timeout=10, memory_limit=1e9):
        """Execute test code in isolated subprocess"""
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": "."}
        )
        return result
    
    @pytest.mark.isolate(timeout=30, mem_limit=2**30)  # 1GB memory limit
    def test_memory_intensive_operation(self):
        """Test with resource constraints"""
        # Your test code here
        pass
```

### 3. **NumPy's Module-Level Testing Pattern**

NumPy's approach involves creating a test runner at the module level with specialized handling for C extensions:

```python
# __init__.py in each module
from numpy._pytesttester import PytestTester
test = PytestTester(__name__).test
del PytestTester

# tests/test_module.py
import pytest
import gc
import warnings

class TestCExtensionModule:
    """Test suite for C extension modules"""
    
    @classmethod
    def setup_class(cls):
        """Setup once for all tests in class"""
        # Store original state
        cls._original_state = capture_global_state()
        
    @classmethod
    def teardown_class(cls):
        """Cleanup after all tests"""
        restore_global_state(cls._original_state)
        gc.collect()  # Force garbage collection
        
    def setup_method(self, method):
        """Setup before each test method"""
        # Clear any cached imports
        import sys
        self._cached_modules = list(sys.modules.keys())
        
    def teardown_method(self, method):
        """Cleanup after each test"""
        import sys
        # Remove any modules imported during test
        for key in list(sys.modules.keys()):
            if key not in self._cached_modules and 'nautilus' in key:
                del sys.modules[key]
        gc.collect()
```

### 4. **Worker-Based Parallel Testing (pytest-xdist pattern)**

pytest-xdist extends pytest with distributed testing across multiple CPUs, spawning worker processes to handle tests:

```python
# conftest.py
def pytest_configure(config):
    """Configure pytest with xdist support"""
    import multiprocessing
    
    # Auto-detect CPUs for parallel execution
    if hasattr(config.option, 'numprocesses'):
        if config.option.numprocesses == 'auto':
            config.option.numprocesses = multiprocessing.cpu_count()

@pytest.fixture
def worker_id(request):
    """Get unique worker ID for parallel tests"""
    if hasattr(request.config, 'workerinput'):
        # Running with xdist
        return request.config.workerinput['workerid']
    else:
        # Single process mode
        return 'master'

# Use worker_id to create isolated resources
@pytest.fixture
def isolated_database(worker_id):
    """Create per-worker database for testing"""
    db_name = f"test_db_{worker_id}"
    connection = create_database(db_name)
    yield connection
    drop_database(db_name)
```

### 5. **Async Event Loop Isolation Pattern**

For async code with event loops, use isolated event loop fixtures to prevent conflicts:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def event_loop():
    """Create isolated event loop per test"""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    try:
        # Cancel all tasks
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    finally:
        loop.close()

@pytest.fixture
async def isolated_async_client(event_loop):
    """Async client with proper cleanup"""
    from httpx import AsyncClient
    async with AsyncClient() as client:
        yield client
    # Cleanup happens automatically

@pytest.mark.asyncio
async def test_async_trading_operation(isolated_async_client):
    """Test with isolated async environment"""
    response = await isolated_async_client.get("/api/markets")
    assert response.status_code == 200
```

### 6. **Mock Factory Pattern for Complex Dependencies**

Using pytest-mock's mocker fixture provides clean mock management:

```python
# tests/factories/mock_factory.py
from unittest.mock import Mock, AsyncMock, patch
import pytest

class NautilusTestFactory:
    """Factory for creating test doubles"""
    
    @staticmethod
    @pytest.fixture
    def mock_trading_engine(mocker):
        """Create fully mocked trading engine"""
        engine = mocker.MagicMock()
        
        # Mock C extension components
        with mocker.patch('nautilus_trader.core._cython_module'):
            engine.clock = mocker.Mock()
            engine.cache = mocker.Mock()
            engine.msgbus = mocker.Mock()
            
            # Setup return values
            engine.clock.timestamp_ns.return_value = 1234567890
            engine.cache.instruments.return_value = []
            
        return engine
    
    @staticmethod
    def create_test_data_engine():
        """Create test data engine with mocked C components"""
        with patch('nautilus_trader.data.engine.DataEngine._init_c_components'):
            engine = Mock()
            engine.start = Mock()
            engine.stop = Mock()
            engine.process = Mock()
            return engine
```

### 7. **Test Segmentation Pattern**

Structure your tests to run in separate processes based on their characteristics:

```python
# Makefile
.PHONY: test-unit test-integration test-c-extensions

test-unit:  # Pure Python, can run in parallel
	pytest tests/unit -n auto --tb=short

test-c-extensions:  # Need isolation
	pytest tests/extensions --forked --tb=short

test-integration:  # May need sequential execution
	pytest tests/integration --tb=short

test-all:  # Run in separate processes
	$(MAKE) test-unit
	$(MAKE) test-c-extensions  
	$(MAKE) test-integration

# pytest.ini with markers
[tool:pytest]
markers =
    unit: Pure Python unit tests
    requires_isolation: Tests needing subprocess isolation
    integration: Integration tests
    slow: Long-running tests
    
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

### 8. **State Cleanup Pattern**

Comprehensive cleanup between tests:

```python
# conftest.py
import pytest
import gc
import warnings

@pytest.fixture(autouse=True)
def cleanup_global_state():
    """Cleanup fixture that runs for every test"""
    # Capture initial state
    import sys
    initial_modules = set(sys.modules.keys())
    initial_warnings = warnings.filters[:]
    
    yield
    
    # Cleanup after test
    # 1. Remove imported modules
    current_modules = set(sys.modules.keys())
    new_modules = current_modules - initial_modules
    for module in new_modules:
        if 'nautilus' in module or 'test' in module:
            sys.modules.pop(module, None)
    
    # 2. Reset warnings
    warnings.filters[:] = initial_warnings
    
    # 3. Force garbage collection
    gc.collect()
    
    # 4. Clear any C extension caches if available
    try:
        from nautilus_trader.core import clear_global_cache
        clear_global_cache()
    except ImportError:
        pass
```

### 9. **Parametrized Testing for C Extensions**

Both NumPy and pytest recommend using parametrized tests for comprehensive coverage:

```python
import pytest
import numpy as np

@pytest.mark.parametrize("dtype", [np.float32, np.float64, np.int32])
@pytest.mark.parametrize("size", [10, 100, 1000])
def test_array_operations(dtype, size):
    """Test with different data types and sizes"""
    data = np.random.randn(size).astype(dtype)
    result = your_c_extension_function(data)
    assert result.dtype == dtype
    assert len(result) == size

# For Nautilus-specific testing
@pytest.mark.parametrize("venue,account_type", [
    ("BINANCE", "CASH"),
    ("INTERACTIVE_BROKERS", "MARGIN"),
])
def test_venue_configurations(venue, account_type, mock_trading_engine):
    """Test different venue configurations"""
    engine = mock_trading_engine
    engine.add_venue(venue, account_type=account_type)
    assert engine.venues[venue].account_type == account_type
```

## Best Practices Summary

1. **Use pytest-xdist with --forked** for parallel, isolated test execution
2. **Segment tests** into unit/integration/c-extension categories
3. **Mock C extensions** at the Python boundary when possible
4. **Run different test types in separate processes** via Makefile/script
5. **Implement comprehensive cleanup** fixtures for state management
6. **Use subprocess.run()** for tests that absolutely need isolation
7. **Create isolated event loops** for async tests
8. **Leverage pytest markers** to categorize and selectively run tests
9. **Use parametrization** extensively for thorough testing
10. **Monitor and limit resources** with tools like pytest-isolate

These patterns are battle-tested by projects like NumPy, pandas, and scikit-learn that face similar challenges with C extensions and global state management.