#!/usr/bin/env python3
"""
Claude PR Reviewer - Automated code review using Claude AI.

This script fetches PR diff, analyzes the changes using Claude,
and posts review comments back to the GitHub PR.
"""

import os
import sys
import json
import requests
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class ReviewSeverity(Enum):
    """Severity levels for review comments."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ReviewComment:
    """Represents a code review comment."""
    path: str
    line: int
    body: str
    severity: ReviewSeverity = ReviewSeverity.INFO


class ClaudePRReviewer:
    """Main class for Claude-powered PR reviews."""

    def __init__(self):
        """Initialize the reviewer with environment variables."""
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.pr_number = int(os.getenv("PR_NUMBER", "0"))
        self.repo_owner = os.getenv("REPO_OWNER")
        self.repo_name = os.getenv("REPO_NAME")
        self.base_sha = os.getenv("BASE_SHA")
        self.head_sha = os.getenv("HEAD_SHA")
        self.changed_files = os.getenv("CHANGED_FILES", "").split()

        # Validate required environment variables
        self._validate_environment()

        self.github_api_base = (
            f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}"
        )

    def _validate_environment(self) -> None:
        """Validate required environment variables."""
        required_vars = {
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "GITHUB_TOKEN": self.github_token,
            "REPO_OWNER": self.repo_owner,
            "REPO_NAME": self.repo_name,
            "BASE_SHA": self.base_sha,
            "HEAD_SHA": self.head_sha,
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        
        if missing_vars:
            print(f"❌ Missing required environment variables: {missing_vars}")
            for var, value in required_vars.items():
                print(f"  {var}: {'✓ Set' if value else '✗ Missing'}")
            raise ValueError(f"Missing required environment variables: {missing_vars}")

    def get_pr_info(self) -> Dict[str, str]:
        """Fetch PR title, description, and metadata."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        url = f"{self.github_api_base}/pulls/{self.pr_number}"
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch PR info: {response.status_code} - {response.text}"
            )

        pr_data = response.json()
        return {
            "title": pr_data.get("title", ""),
            "body": pr_data.get("body", "") or "No description provided",
            "user": pr_data.get("user", {}).get("login", "unknown"),
            "base_branch": pr_data.get("base", {}).get("ref", "main"),
            "head_branch": pr_data.get("head", {}).get("ref", "feature"),
            "draft": pr_data.get("draft", False),
            "labels": [label["name"] for label in pr_data.get("labels", [])],
        }

    def get_pr_diff(self) -> str:
        """Fetch the complete diff for the PR."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3.diff",
        }

        url = f"{self.github_api_base}/pulls/{self.pr_number}"
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch PR diff: {response.status_code} - {response.text}"
            )

        return response.text

    def get_file_content(self, file_path: str, sha: str) -> Optional[str]:
        """Get the content of a file at a specific commit."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3.raw",
        }

        url = f"{self.github_api_base}/contents/{file_path}?ref={sha}"
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            return response.text
        else:
            return None

    def _categorize_files(self) -> Dict[str, List[str]]:
        """Categorize changed files by type."""
        categories = {
            "tests": [],
            "strategies": [],
            "api": [],
            "models": [],
            "services": [],
            "database": [],
            "config": [],
            "docs": [],
            "cli": [],
            "other": []
        }
        
        for file_path in self.changed_files:
            if "test" in file_path or "tests" in file_path:
                categories["tests"].append(file_path)
            elif "strategies" in file_path or "strategy" in file_path:
                categories["strategies"].append(file_path)
            elif "api" in file_path or "routers" in file_path:
                categories["api"].append(file_path)
            elif "models" in file_path or "schemas" in file_path:
                categories["models"].append(file_path)
            elif "services" in file_path or "service" in file_path:
                categories["services"].append(file_path)
            elif "db" in file_path or "database" in file_path or "migrations" in file_path:
                categories["database"].append(file_path)
            elif "cli" in file_path or "commands" in file_path:
                categories["cli"].append(file_path)
            elif any(cfg in file_path for cfg in [".yaml", ".yml", ".env", "config"]):
                categories["config"].append(file_path)
            elif any(doc in file_path for doc in [".md", "docs", "README"]):
                categories["docs"].append(file_path)
            else:
                categories["other"].append(file_path)
                
        return {k: v for k, v in categories.items() if v}

    def _build_review_prompt(
        self, 
        diff_content: str, 
        file_contents: Dict[str, str], 
        pr_info: Dict[str, str],
        file_categories: Dict[str, List[str]]
    ) -> str:
        """Build a comprehensive review prompt with project context."""
        
        return f"""You are an expert code reviewer for the Nautilus Trader Backtesting System, a production-grade algorithmic trading platform.

# PROJECT CONTEXT

## System Overview
- **Purpose**: Backtesting trading strategies on historical market data with realistic commission/slippage modeling
- **Core Framework**: Nautilus Trader (event-driven backtesting engine written in Rust/Cython)
- **Data Source**: Interactive Brokers TWS/Gateway with rate limiting (50 req/sec)
- **Architecture**: CLI-first implementation with FastAPI REST API (deferred to future phase)
- **Target Users**: Quantitative traders and developers
- **Current Phase**: CLI implementation (Phase 1)

## Technical Stack
- **Language**: Python 3.11+ (strict typing required)
- **Core Dependencies**: nautilus_trader[ib], Click (CLI), FastAPI, Pydantic v2, SQLAlchemy 2.0+
- **Database**: PostgreSQL with TimescaleDB for time-series data
- **Cache**: Redis for performance optimization
- **Testing**: pytest with 80% minimum coverage
- **Package Manager**: UV exclusively (never edit pyproject.toml directly)
- **Code Quality**: ruff for formatting/linting, mypy for type checking
- **Logging**: structlog with correlation IDs

## Project Structure
```
src/
├── cli/          # Click-based CLI commands
│   ├── commands/ # Individual command modules
│   └── main.py   # Entry point
├── core/         # Trading strategies and business logic
│   └── strategies/
├── models/       # Pydantic models and schemas
├── services/     # IBKR client, backtesting engine
├── db/           # Database models and migrations
└── utils/        # Shared utilities

tests/            # Mirror src structure with test_ prefix
scripts/          # Automation and data management
configs/          # Example YAML configurations
```

# CONSTITUTIONAL REQUIREMENTS (NON-NEGOTIABLE)

## 1. Test-Driven Development (CRITICAL)
- **Tests MUST be written BEFORE implementation**
- **Red-Green-Refactor cycle is mandatory**
- **No feature is complete without tests**
- **Minimum 80% coverage on critical paths**
- **Test files must exist for every module**
- **Test naming**: test_<module>.py
- **Function naming**: test_<function>_<scenario>_<expected_result>

## 2. Code Structure Limits
- **Files: Maximum 500 lines** (split into modules if approaching)
- **Functions: Maximum 50 lines** (single responsibility)
- **Classes: Maximum 100 lines** (single concept)
- **Line length: Maximum 100 characters**
- **Cyclomatic complexity: Maximum 10**

## 3. Type Safety & Documentation
- **All functions require type hints (PEP 484)**
- **Google-style docstrings with examples**
- **Complex logic needs inline comments with "# Reason:" prefix**
- **Mypy validation must pass**
- **Return types must be explicit**

## 4. Dependency Management
- **Use UV commands exclusively**: uv add, uv remove, uv sync
- **Never modify pyproject.toml directly**
- **Pin production dependencies to specific versions**
- **Separate dev/test/prod dependencies**
- **Dependencies must be actively maintained (commits within 6 months)**

## 5. Error Handling & Logging
- **Custom exceptions for domain errors**
- **Never bare except: clauses**
- **Structured logging with structlog**
- **Correlation IDs for request tracing**
- **Log levels: DEBUG (dev only), INFO (key events), WARNING (recoverable), ERROR (failures)**

# TRADING DOMAIN CONTEXT

## Core Entities (Data Model)
1. **TradingStrategy**: Entry/exit rules, parameters, signal generation
   - Fields: id, name, strategy_type, parameters, created_at, is_active
   - Validation: Name uniqueness, parameter schema matching

2. **MarketData**: OHLCV bars with timestamps
   - Fields: instrument_id, timestamp, open, high, low, close, volume, timeframe
   - Validation: High >= max(open, close), Low <= min(open, close)

3. **Trade**: Buy/sell transactions
   - Fields: entry_time, entry_price, exit_time, exit_price, quantity, side, commission, pnl
   - States: Open → Closed

4. **Portfolio**: Holdings and cash balance
   - Fields: cash_balance, positions, total_value, margin_used, buying_power
   - Snapshots: Captured at each trade event

5. **BacktestResult**: Complete run with metrics
   - Metrics: CAGR, Sharpe ratio, Sortino ratio, max drawdown, win rate
   - Validation: Realistic ranges (Sharpe -3 to 5)

6. **Instrument**: Trading specifications
   - Fields: symbol, exchange, currency, tick_size, lot_size, trading_hours

## Strategy Types
- **SMA Crossover**: Fast/slow moving average crossover signals
- **Mean Reversion**: Z-score based entry/exit (lookback period, entry/exit thresholds)
- **Momentum**: RSI-based momentum trading (period, overbought/oversold levels)

## Critical Trading Logic Areas
- **Position Sizing**: 1% risk per trade default, max 10% of portfolio
- **Commission Models**: Fixed per share ($0.005) or percentage
- **Slippage**: 1 basis point default
- **FX Conversion**: Handle non-USD instruments
- **Multi-Timeframe**: Support 1m, 5m, 1h, daily bars
- **Order Types**: Market and limit orders
- **Rate Limiting**: IBKR 50 requests/second

## Performance Requirements
- **Backtest Speed**: <5s for 1 year simple strategy
- **Data Loading**: <2s for 1 year of 1-minute bars
- **Memory Usage**: <2GB for typical backtests
- **API Response**: <200ms simple, <1s complex

# REVIEW CHECKLIST

## 🔴 CRITICAL (Must Fix)
□ **TDD Compliance**: Do test files exist BEFORE implementation files?
□ **Test Coverage**: Are critical paths covered with tests?
□ **Type Safety**: Are all functions typed? Does mypy pass?
□ **File Size**: Are any files approaching 500 lines?
□ **Dependencies**: Were dependencies added via UV commands?
□ **Security**: No hardcoded secrets, API keys, or passwords?

## 🟡 IMPORTANT (Should Fix)
□ **Trading Logic**: 
  - Is position sizing correct (1% risk)?
  - Are commissions and slippage applied?
  - Is PnL calculation accurate?
  - Are trading hours respected?
□ **Error Handling**: 
  - Are trading errors properly caught?
  - Is there proper validation for negative prices?
  - Are insufficient funds handled?
□ **Async Patterns**: Is async/await used for I/O operations?
□ **Nautilus Integration**: Are framework patterns used correctly?
□ **Database**: 
  - Are queries optimized?
  - Do migrations handle rollback?
  - Is TimescaleDB used for time-series?

## 🟢 QUALITY (Nice to Have)
□ **Documentation**: Are docstrings complete with examples?
□ **Performance**: Are expensive operations cached?
□ **Logging**: Is structured logging with correlation IDs used?
□ **Code Style**: Does code follow PEP8 and project conventions?
□ **CLI**: Are commands intuitive with --help text?

# CLI COMMAND STRUCTURE
```
ntrader
├── strategy    # Strategy management
│   ├── list
│   ├── create
│   ├── show
│   └── validate
├── backtest    # Backtest execution
│   ├── run
│   ├── list
│   ├── show
│   └── compare
├── data        # Data management
│   ├── connect (IBKR)
│   ├── fetch
│   ├── import (CSV)
│   └── verify
├── report      # Report generation
│   ├── generate (HTML/CSV/JSON)
│   ├── summary
│   └── trades
└── config      # Configuration
    ├── init
    └── show
```

# FILE CATEGORIES IN THIS PR
{json.dumps(file_categories, indent=2)}

# PULL REQUEST DETAILS
- **Title**: {pr_info['title']}
- **Author**: {pr_info['user']}
- **Branch**: {pr_info['head_branch']} → {pr_info['base_branch']}
- **Draft**: {pr_info.get('draft', False)}
- **Labels**: {', '.join(pr_info.get('labels', []))}
- **Description**: {pr_info['body']}

# FILE CONTENTS (First 5000 chars per file)
{json.dumps(file_contents, indent=2)}

# PULL REQUEST DIFF
```diff
{diff_content}
```

# REVIEW INSTRUCTIONS

Please provide a thorough code review following this structure:

## 1. TDD Compliance Check (CRITICAL)
- List any implementation files WITHOUT corresponding test files
- Identify tests that may have been written AFTER implementation
- Check if test coverage appears adequate for critical paths
- Verify test naming conventions (test_<module>.py)

## 2. Constitutional Violations
- List any violations of the non-negotiable requirements
- Specify exact line numbers and files
- Check file/function/class size limits
- Verify UV usage for dependencies

## 3. Trading Logic Review
- Assess correctness of trading strategy implementations
- Check position sizing (1% risk, max 10% portfolio)
- Verify commission calculations ($0.005/share or percentage)
- Check slippage implementation (1 basis point)
- Validate PnL calculations
- Review order execution logic

## 4. Code Quality Assessment
- Type hints completeness (all functions typed?)
- Documentation quality (Google-style docstrings?)
- Error handling robustness
- Performance considerations
- Async/await usage for I/O

## 5. Positive Highlights
- Acknowledge good practices and well-written code
- Point out clever solutions that maintain simplicity
- Highlight good test coverage

## 6. Actionable Improvements
- Provide specific code examples for fixes
- Prioritize by severity (Critical → Important → Quality)
- Include line numbers for all suggestions

Format your response with clear sections and use markdown for code examples.
Keep the review constructive, specific, and focused on the most important issues.
Focus on trading domain correctness and TDD compliance above all else."""

    def analyze_with_claude(
        self, diff_content: str, file_contents: Dict[str, str], pr_info: Dict[str, str]
    ) -> str:
        """Send the diff to Claude for analysis with improved context."""
        
        # Categorize files for better context
        file_categories = self._categorize_files()
        
        # Build the comprehensive prompt
        context = self._build_review_prompt(
            diff_content, file_contents, pr_info, file_categories
        )

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": context}],
        }

        response = requests.post(
            "https://api.anthropic.com/v1/messages", 
            headers=headers, 
            json=payload,
            timeout=60
        )

        if response.status_code != 200:
            raise Exception(
                f"Claude API error: {response.status_code} - {response.text}"
            )

        result = response.json()
        return result["content"][0]["text"]

    def post_review_comment(self, review_body: str) -> None:
        """Post the review as a PR comment."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Create a formatted review comment
        formatted_review = f"""## 🤖 Claude AI Code Review

{review_body}

---
📋 **Review Context**
- Constitution Version: 1.0.1
- Review Focus: TDD Compliance, Trading Logic, Type Safety
- Project: Nautilus Trader Backtesting System
- Phase: CLI Implementation (Phase 1)

*This review was generated automatically by Claude AI. For questions about this review, please check the GitHub Action logs or contact the maintainers.*
"""

        payload = {"body": formatted_review}

        url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code != 201:
            print(f"Failed to post comment: {response.status_code} - {response.text}")
            sys.exit(1)

        print("✅ Claude review posted successfully!")

    def post_error_comment(self, error_message: str) -> None:
        """Post an error comment to the PR."""
        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

            error_comment = f"""## 🤖 Claude AI Review Error

⚠️ **Error occurred during automated review**

{error_message}

**Troubleshooting Steps:**
1. Verify ANTHROPIC_API_KEY is set in repository secrets
2. Check GitHub Action logs for detailed error messages
3. Ensure PR has proper permissions for bot comments
4. Verify the changed files are accessible

For assistance, contact the repository maintainers.
"""
            payload = {"body": error_comment}
            url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code == 201:
                print("✅ Error comment posted successfully")
            else:
                print(f"❌ Failed to post error comment: {response.status_code}")

        except Exception as e:
            print(f"❌ Failed to post error comment: {e}")

    def _should_review_file(self, file_path: str) -> bool:
        """Determine if a file should be reviewed."""
        # Skip certain file types
        skip_extensions = {
            '.json', '.lock', '.toml', '.txt', '.csv', '.log',
            '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico'
        }
        skip_dirs = {
            'node_modules', '__pycache__', '.git', 'venv', '.venv',
            'dist', 'build', '.pytest_cache', '.mypy_cache'
        }
        
        # Check if file should be skipped
        for skip_dir in skip_dirs:
            if skip_dir in file_path:
                return False
                
        # Skip based on extension
        for ext in skip_extensions:
            if file_path.endswith(ext):
                return False
        
        # Skip if it's just a deletion
        if not os.path.exists(file_path) and file_path not in self.changed_files:
            return False
                
        return True

    def _check_tdd_compliance(self) -> List[str]:
        """Check if tests exist for implementation files."""
        violations = []
        
        # Separate test and implementation files
        test_files = set()
        impl_files = set()
        
        for file_path in self.changed_files:
            if '/test' in file_path or file_path.startswith('test'):
                test_files.add(file_path)
            elif file_path.endswith('.py') and not file_path.endswith('__init__.py'):
                impl_files.add(file_path)
        
        # Check if each implementation file has a corresponding test
        for impl_file in impl_files:
            # Extract module name
            module_name = os.path.basename(impl_file).replace('.py', '')
            test_name = f"test_{module_name}.py"
            
            # Check if test exists in changed files
            has_test = any(test_name in test_file for test_file in test_files)
            
            if not has_test and module_name not in ['__main__', 'setup', 'config']:
                violations.append(f"No test file found for {impl_file}")
        
        return violations

    def run_review(self) -> None:
        """Run the complete review process."""
        try:
            print(f"🔍 Starting Claude review for PR #{self.pr_number}")
            print(f"📁 Changed files: {len(self.changed_files)} files")
            print("🔧 Environment check:")
            print(
                f"  - ANTHROPIC_API_KEY: {'✓ Set' if self.anthropic_api_key else '✗ Missing'}"
            )
            print(f"  - GITHUB_TOKEN: {'✓ Set' if self.github_token else '✗ Missing'}")
            print(f"  - PR_NUMBER: {self.pr_number}")
            print(f"  - REPO: {self.repo_owner}/{self.repo_name}")

            # Early exit if API key is missing
            if not self.anthropic_api_key:
                print("❌ ANTHROPIC_API_KEY is required but not set")
                self.post_error_comment(
                    "**Configuration Error**: ANTHROPIC_API_KEY secret is not configured in repository settings.\n\n"
                    "Please add the API key to repository secrets:\n"
                    "1. Go to Settings → Secrets and variables → Actions\n"
                    "2. Add new secret named `ANTHROPIC_API_KEY`\n"
                    "3. Re-run this workflow"
                )
                return

            # Get PR information
            print("📋 Fetching PR details...")
            pr_info = self.get_pr_info()

            # Skip draft PRs unless explicitly requested
            if pr_info.get("draft", False) and "review-draft" not in pr_info.get("labels", []):
                print("⏭️  Skipping review for draft PR (add 'review-draft' label to force review)")
                return

            # Quick TDD compliance check
            print("🧪 Checking TDD compliance...")
            tdd_violations = self._check_tdd_compliance()
            if tdd_violations:
                print(f"⚠️  TDD violations detected: {len(tdd_violations)} files without tests")

            # Get PR diff
            print("📥 Fetching PR diff...")
            diff_content = self.get_pr_diff()
            
            # Check diff size
            diff_size = len(diff_content)
            print(f"📊 Diff size: {diff_size:,} characters")
            
            if diff_size > 100000:  # ~100KB
                print("⚠️  Large diff detected, limiting file content fetching")
                max_files = 5
            elif diff_size > 50000:
                max_files = 10
            else:
                max_files = 15

            # Get file contents for better context
            print("📄 Fetching file contents...")
            file_contents = {}
            files_to_review = [f for f in self.changed_files if self._should_review_file(f)]
            
            print(f"📝 Files to review: {len(files_to_review)}")
            for file_path in files_to_review[:max_files]:
                try:
                    content = self.get_file_content(file_path, self.head_sha)
                    if content:
                        # Limit content size to avoid token limits
                        file_contents[file_path] = content[:5000]
                        print(f"  ✓ {file_path} ({len(content)} chars)")
                    else:
                        file_contents[file_path] = "Content unavailable"
                        print(f"  ⚠️  {file_path} (unavailable)")
                except Exception as e:
                    print(f"  ✗ {file_path}: {e}")
                    file_contents[file_path] = "Error fetching content"

            # Analyze with Claude
            print("🧠 Analyzing with Claude...")
            print(f"  📝 PR Title: {pr_info['title']}")
            print(f"  👤 Author: {pr_info['user']}")
            print(f"  🏷️  Labels: {', '.join(pr_info.get('labels', [])) or 'None'}")
            
            review = self.analyze_with_claude(diff_content, file_contents, pr_info)

            # Post review
            print("💬 Posting review comment...")
            self.post_review_comment(review)

            print("🎉 Review process completed successfully!")

        except requests.exceptions.Timeout:
            print("❌ Request timeout occurred")
            self.post_error_comment(
                "**Timeout Error**: The review request took too long to process.\n\n"
                "This might be due to:\n"
                "- Large PR with many changes\n"
                "- Temporary API issues\n\n"
                "Please try re-running the workflow or reduce the PR size."
            )
            sys.exit(1)
            
        except Exception as e:
            print(f"❌ Error during review: {e}")
            import traceback
            traceback.print_exc()
            
            # Post error comment with more context
            error_details = str(e)[:500]  # Limit error message length
            self.post_error_comment(
                f"**Unexpected Error**: An error occurred while generating the automated review.\n\n"
                f"```\n{error_details}\n```\n\n"
                f"Please check the GitHub Action logs for full details."
            )
            sys.exit(1)


def main():
    """Main entry point."""
    print("🚀 Claude PR Reviewer starting...")
    print(f"📦 Version: 2.0.0")
    print(f"🏗️  Project: Nautilus Trader Backtesting System")
    print(f"📐 Constitution: v1.0.1 (TDD Mandatory)")
    
    try:
        reviewer = ClaudePRReviewer()
        reviewer.run_review()
    except KeyboardInterrupt:
        print("\n⚠️  Review cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
