# Makefile
# glibc >= 2.41 (e.g. Ubuntu 26.04) refuses to load nautilus_trader's compiled extensions
# ("cannot enable executable stack as shared object requires: Invalid argument") unless the
# loader is told to allow it. glibc reads GLIBC_TUNABLES once, at process start, so it is
# exported here for every recipe below (uv, pytest, uvicorn and anything they spawn).
# Linux only: macOS has no glibc, and older glibc ignores a tunable it does not know.
# An execstack value already set in your shell wins; other GLIBC_TUNABLES entries are kept.
ifeq ($(shell uname -s),Linux)
ifeq ($(findstring glibc.rtld.execstack,$(GLIBC_TUNABLES)),)
export GLIBC_TUNABLES := $(if $(GLIBC_TUNABLES),$(GLIBC_TUNABLES):)glibc.rtld.execstack=2
endif
endif

.PHONY: help web dev build-css test-unit test-component test-integration test-e2e test-all test-coverage clean format lint typecheck install-hooks

help:
	@echo "Web UI:"
	@echo "  make dev               - Build CSS, then launch the web dashboard (first run / after style changes)"
	@echo "  make web               - Launch the web dashboard (http://127.0.0.1:8000; WEB_PORT=<n> to change)"
	@echo "  make build-css         - Build Tailwind CSS only"
	@echo ""
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
	@echo "  make install-hooks     - Install git pre-commit hook (run once per clone)"

build-css:
	@echo "🎨 Building Tailwind CSS..."
	./scripts/build-css.sh

# Override the port if 8000 is taken: make web WEB_PORT=3001
WEB_PORT ?= 8000

web:
	@echo "🚀 Launching web dashboard at http://127.0.0.1:$(WEB_PORT) ..."
	uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port $(WEB_PORT)

dev: build-css web

test-unit:
	@echo "🧪 Running unit tests (pure Python, no Nautilus)..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@uv run pytest tests/unit -v -n auto --tb=short
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ Unit tests complete"
	@echo ""

test-component:
	@echo "🧪 Running component tests (test doubles)..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@uv run pytest tests/component -v -n auto --tb=short
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ Component tests complete"
	@echo ""

test-integration:
	@echo "🧪 Running integration tests (subprocess isolated)..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	# --forked flag: Each test runs in isolated subprocess
	# WHY: Prevents C extension crashes (Nautilus) from cascading to other tests
	# WHEN: Required for integration tests with real Nautilus components
	# Reference: design.md Section 2.1 - Test Isolation Strategy
	@uv run pytest tests/integration -v -n auto --forked --tb=short
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ Integration tests complete"
	@echo ""

test-e2e:
	@echo "🧪 Running end-to-end tests (sequential)..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@uv run pytest tests/e2e -v --tb=short
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ E2E tests complete"
	@echo ""

test-all:
	@echo "🧪 Running all tests..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@uv run pytest tests -v -n auto --tb=short
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ All tests complete"
	@echo ""

test-coverage:
	@echo "📊 Running tests with coverage..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@uv run pytest tests --cov=src/core --cov=src/strategies --cov-report=html --cov-report=term -v --tb=short
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "📊 Coverage report: htmlcov/index.html"
	@echo ""

format:
	@echo "🎨 Formatting code..."
	uv run ruff format .

lint:
	@echo "🔍 Linting code..."
	uv run ruff check .

typecheck:
	@echo "🔬 Type checking..."
	uv run mypy src/core src/services src/mcp_server

install-hooks:
	@git config core.hooksPath .githooks
	@echo "✅ Git hooks installed (core.hooksPath → .githooks)"

clean:
	@echo "🧹 Cleaning up..."
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	@echo "✅ Clean complete"
