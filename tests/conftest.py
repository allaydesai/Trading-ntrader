"""Pytest configuration and fixtures."""

import gc
from pathlib import Path

import pytest


@pytest.fixture
def project_root():
    """Get project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def test_data_dir(project_root):
    """Get test data directory."""
    return project_root / "data"


@pytest.fixture(autouse=True)
def cleanup():
    """
    Auto-cleanup between tests.

    Runs after every test to:
    - Force garbage collection (clears C extension refs)
    - Prevent state leakage between tests
    """
    yield  # Test runs here
    gc.collect()  # Force cleanup
