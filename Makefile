# Makefile
.PHONY: help test-unit test-component test-integration test-e2e test-all test-coverage clean format lint typecheck

help:
	@echo "Test Commands:"
	@echo "  make test-unit         - Run unit tests (fast, <5s)"
	@echo "  make test-component    - Run component tests (<10s)"
	@echo "  make test-integration  - Run integration tests (<2min)"
	@echo "  make test-e2e          - Run end-to-end tests"
	@echo "  make test-all          - Run all tests"
	@echo "  make test-coverage     - Run tests with coverage report"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format            - Format code with ruff"
	@echo "  make lint              - Lint code with ruff"
	@echo "  make typecheck         - Type check with mypy"

test-unit:
	@echo "🧪 Running unit tests (pure Python, no Nautilus)..."
	uv run pytest tests/unit -v -n auto
	@echo "✅ Unit tests complete"

test-component:
	@echo "🧪 Running component tests (test doubles)..."
	uv run pytest tests/component -v -n auto
	@echo "✅ Component tests complete"

test-integration:
	@echo "🧪 Running integration tests (subprocess isolated)..."
	# --forked flag: Each test runs in isolated subprocess
	# WHY: Prevents C extension crashes (Nautilus) from cascading to other tests
	# WHEN: Required for integration tests with real Nautilus components
	# Reference: design.md Section 2.1 - Test Isolation Strategy
	uv run pytest tests/integration -v -n auto --forked
	@echo "✅ Integration tests complete"

test-e2e:
	@echo "🧪 Running end-to-end tests (sequential)..."
	uv run pytest tests/e2e -v
	@echo "✅ E2E tests complete"

test-all:
	@echo "🧪 Running all tests..."
	uv run pytest tests -v -n auto
	@echo "✅ All tests complete"

test-coverage:
	@echo "📊 Running tests with coverage..."
	uv run pytest tests --cov=src/core --cov=src/strategies --cov-report=html --cov-report=term
	@echo "📊 Coverage report: htmlcov/index.html"

format:
	@echo "🎨 Formatting code..."
	uv run ruff format .

lint:
	@echo "🔍 Linting code..."
	uv run ruff check .

typecheck:
	@echo "🔬 Type checking..."
	uv run mypy src/core src/strategies

clean:
	@echo "🧹 Cleaning up..."
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	@echo "✅ Clean complete"
