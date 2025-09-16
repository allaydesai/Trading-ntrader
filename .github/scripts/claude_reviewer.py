#!/usr/bin/env python3
"""
Claude PR Reviewer - Automated code review using Claude AI.

This script fetches PR diff, analyzes the changes using Claude,
and posts review comments back to the GitHub PR.
"""

import os
import sys
import json
import time
import requests
import re
from typing import Dict, List, Optional, Tuple, Set
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
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.text
        except:
            pass
        return None

    def _categorize_files(self) -> Dict[str, List[str]]:
        """Categorize changed files by type."""
        categories = {
            "tests": [],
            "strategies": [],
            "api": [],
            "cli": [],
            "models": [],
            "services": [],
            "database": [],
            "config": [],
            "docs": [],
            "other": []
        }
        
        for file_path in self.changed_files:
            if "test" in file_path or "tests" in file_path:
                categories["tests"].append(file_path)
            elif "strategies" in file_path or "strategy" in file_path:
                categories["strategies"].append(file_path)
            elif "api" in file_path or "routers" in file_path:
                categories["api"].append(file_path)
            elif "cli" in file_path or "commands" in file_path:
                categories["cli"].append(file_path)
            elif "models" in file_path or "schemas" in file_path:
                categories["models"].append(file_path)
            elif "services" in file_path or "service" in file_path:
                categories["services"].append(file_path)
            elif "db" in file_path or "database" in file_path or "migrations" in file_path:
                categories["database"].append(file_path)
            elif any(cfg in file_path for cfg in [".yaml", ".yml", ".env", "config"]):
                categories["config"].append(file_path)
            elif any(doc in file_path for doc in [".md", "docs", "README"]):
                categories["docs"].append(file_path)
            elif file_path.endswith('.py'):
                categories["other"].append(file_path)
                
        return {k: v for k, v in categories.items() if v}

    def _get_priority_files(self, file_categories: Dict[str, List[str]]) -> List[str]:
        """Identify priority files that need full content review."""
        priority_files = []
        
        # Priority order: tests > strategies > models > services > cli
        priority_categories = ["tests", "strategies", "models", "services", "cli"]
        
        for category in priority_categories:
            if category in file_categories:
                priority_files.extend(file_categories[category])
        
        # Limit to reasonable number but include all critical files
        return priority_files[:20]  # Increased from 10 to 20

    def _fetch_file_contents(self, priority_files: List[str]) -> Dict[str, str]:
        """Fetch full content for priority files."""
        file_contents = {}
        
        for file_path in priority_files:
            # Skip non-Python files
            if not file_path.endswith('.py'):
                continue
                
            print(f"  📄 Fetching: {file_path}")
            content = self.get_file_content(file_path, self.head_sha)
            
            if content:
                # Include full content for smaller files, truncate large ones
                if len(content) < 10000:  # ~10KB files get full content
                    file_contents[file_path] = content
                else:
                    # For larger files, include first 5000 chars + structure
                    file_contents[file_path] = self._extract_file_structure(content)
            else:
                file_contents[file_path] = "# File content unavailable"
        
        return file_contents

    def _extract_file_structure(self, content: str) -> str:
        """Extract important structure from large files."""
        lines = content.split('\n')
        extracted = []
        
        # Include imports
        for line in lines[:50]:
            if line.startswith('import ') or line.startswith('from '):
                extracted.append(line)
            elif line.startswith('class ') or line.startswith('def '):
                extracted.append(line)
        
        # Include class and function definitions
        for i, line in enumerate(lines):
            if line.startswith('class '):
                # Include class definition and docstring
                extracted.extend(lines[i:min(i+10, len(lines))])
            elif line.startswith('def ') and not line.startswith('def _'):
                # Include public function signatures
                extracted.extend(lines[i:min(i+5, len(lines))])
        
        if len(extracted) > 100:
            extracted = extracted[:100] + ["# ... (truncated for length)"]
        
        return '\n'.join(extracted)

    def _build_comprehensive_prompt(
        self, 
        diff_content: str, 
        file_contents: Dict[str, str],
        pr_info: Dict[str, str],
        file_categories: Dict[str, List[str]]
    ) -> str:
        """Build a comprehensive review prompt with full context."""
        
        # Only truncate if absolutely massive
        max_diff_size = 80000  # 80KB - much larger than before
        truncated = False
        if len(diff_content) > max_diff_size:
            # Keep most of the diff
            keep_size = max_diff_size - 5000
            diff_content = (
                diff_content[:keep_size] + 
                "\n\n... [VERY LARGE DIFF - FINAL PORTION TRUNCATED] ...\n\n" + 
                diff_content[-5000:]
            )
            truncated = True
        
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
- **Core Dependencies**: nautilus_trader[ib], Click (CLI), Rich (formatting), FastAPI, Pydantic v2, SQLAlchemy 2.0+
- **Database**: PostgreSQL with TimescaleDB for time-series data
- **Cache**: Redis for performance optimization  
- **Testing**: pytest with 80% minimum coverage (TDD mandatory)
- **Package Manager**: UV exclusively (never edit pyproject.toml directly)
- **Code Quality**: ruff for formatting/linting, mypy for type checking
- **Logging**: structlog with correlation IDs

## Project Structure
```
src/
├── cli/              # Click-based CLI commands
│   ├── commands/     # Individual command modules  
│   ├── formatters.py # Rich output formatting
│   └── main.py       # Entry point
├── core/             # Trading strategies and business logic
│   ├── strategies/   # Strategy implementations
│   └── backtest_runner.py
├── models/           # Pydantic models and schemas
├── services/         # Business services
│   ├── ibkr_client.py     # IBKR connection
│   ├── data_service.py    # Data management
│   ├── performance.py     # Metrics calculation
│   └── reports/           # Report generation
├── db/               # Database models and migrations
└── utils/            # Shared utilities

tests/                # Mirror src structure with test_ prefix
configs/              # Example YAML configurations
scripts/              # Automation and setup
```

# CONSTITUTIONAL REQUIREMENTS (NON-NEGOTIABLE)

## 1. Test-Driven Development (CRITICAL)
- **Tests MUST be written BEFORE implementation**
- **Red-Green-Refactor cycle is mandatory**
- **Each feature starts with a failing test**
- **Minimum 80% coverage on critical paths**
- **Test naming**: test_<module>.py, test_<function>_<scenario>_<expected_result>
- **Use pytest fixtures for shared setup**
- **One test per test function**

## 2. Code Structure Limits  
- **Files: Maximum 500 lines** (split into modules if approaching)
- **Functions: Maximum 50 lines** (single responsibility)
- **Classes: Maximum 100 lines** (single concept)
- **Line length: Maximum 100 characters**
- **Cyclomatic complexity: Maximum 10**

## 3. Type Safety & Documentation
- **All functions require type hints (PEP 484)**
- **Return types must be explicit**
- **Google-style docstrings with Args, Returns, Raises, Example sections**
- **Complex logic needs inline comments with "# Reason:" prefix**
- **Mypy validation must pass with strict mode**

## 4. Error Handling & Logging
- **Custom exception classes for domain errors**
- **Never bare except: clauses**  
- **Structured logging with structlog**
- **Log levels: DEBUG (dev), INFO (events), WARNING (recoverable), ERROR (failures)**
- **Include context: operation, user_id, instrument, timestamp**

# TRADING DOMAIN CONTEXT

## Core Entities
1. **TradingStrategy**
   - Fields: id, name, strategy_type, parameters, created_at, is_active
   - Types: SMA_CROSSOVER, MEAN_REVERSION, MOMENTUM
   - Validation: Name uniqueness, parameter schema matching

2. **MarketData**
   - Fields: instrument_id, timestamp, open, high, low, close, volume, timeframe
   - Timeframes: 1MIN, 5MIN, 1HOUR, DAILY
   - Validation: High >= max(open, close), Low <= min(open, close), Volume >= 0

3. **Trade**
   - Fields: entry_time, entry_price, exit_time, exit_price, quantity, side, commission, pnl
   - States: Open → Closed
   - Validation: Entry price > 0, Quantity > 0, PnL calculated on close

4. **Portfolio**
   - Fields: cash_balance, positions, total_value, margin_used, buying_power
   - Snapshots: At each trade event, daily for reporting
   - Validation: Total value = cash + position values

5. **BacktestResult**  
   - Metrics: CAGR, Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor
   - Validation: Sharpe ratio realistic (-3 to 5), Win rate (0 to 1)
   - Benchmark: Compare against SPY

## Strategy Specifications
### SMA Crossover
- Parameters: fast_period (10-50), slow_period (20-200)
- Signal: BUY when fast > slow, SELL when fast < slow
- Validation: fast_period < slow_period

### Mean Reversion
- Parameters: lookback_period (10-50), entry_zscore (1.5-3), exit_zscore (0-1)
- Signal: BUY when zscore < -entry_zscore, SELL when zscore > exit_zscore
- Validation: entry_zscore > exit_zscore

### Momentum (RSI)
- Parameters: rsi_period (7-21), oversold (20-40), overbought (60-80)
- Signal: BUY when RSI < oversold, SELL when RSI > overbought
- Validation: oversold < overbought

## Critical Trading Rules
- **Position Sizing**: 1% risk per trade default, 10% portfolio max
- **Commission Model**: $0.005/share, $1 minimum, 0.5% max
- **Slippage**: 1 basis point per side
- **FX Conversion**: Handle non-USD instruments properly
- **Trading Hours**: Respect market hours for equities, 24/5 for FX
- **Rate Limiting**: IBKR 50 requests/second with throttling

## CLI Command Structure
```
ntrader
├── strategy
│   ├── list         # List available strategies
│   ├── create       # Create new strategy config
│   ├── show         # Show strategy details
│   └── validate     # Validate strategy parameters
├── backtest
│   ├── run          # Run backtest
│   ├── run-config   # Run from YAML config
│   ├── list         # List past backtests
│   └── compare      # Compare multiple backtests
├── data
│   ├── connect      # Connect to IBKR
│   ├── fetch        # Fetch historical data
│   ├── import       # Import CSV data
│   └── verify       # Verify data completeness
└── report
    ├── generate     # Generate HTML/CSV/JSON
    ├── summary      # Console summary
    └── trades       # Export trades
```

# PR INFORMATION
- **Title**: {pr_info['title']}
- **Author**: {pr_info['user']}  
- **Branch**: {pr_info['head_branch']} → {pr_info['base_branch']}
- **Draft**: {pr_info.get('draft', False)}
- **Description**: {pr_info['body']}

# FILES CHANGED BY CATEGORY
{json.dumps(file_categories, indent=2)}

# FILE CONTENTS (Priority Files with Full/Structured Content)
{json.dumps(file_contents, indent=2) if file_contents else "No priority files fetched"}

# COMPLETE DIFF {'[TRUNCATED]' if truncated else '[FULL]'}
```diff
{diff_content}
```

# REVIEW INSTRUCTIONS

Please provide a comprehensive code review following this structure:

## 1. TDD Compliance Assessment (CRITICAL)
- Check if test files exist for ALL implementation files
- Verify tests appear to be written BEFORE implementation (check git history if visible)
- Assess test coverage for critical paths
- Check test naming conventions and structure
- Verify fixtures and mocking are used appropriately

## 2. Constitutional Compliance
- File/function/class size violations (with line numbers)
- Type hints missing (list specific functions)
- Documentation gaps (which functions lack docstrings)
- UV usage for dependencies (check pyproject.toml changes)
- Error handling issues (bare excepts, missing custom exceptions)

## 3. Trading Logic Correctness  
- Position sizing calculations (verify 1% risk, 10% max)
- Commission and slippage implementation
- PnL calculations (check for accuracy)
- Strategy implementation correctness
- Order execution logic
- Market hours handling
- Data validation and edge cases

## 4. Code Quality & Best Practices
- Async/await usage for I/O operations
- Database query optimization
- Caching for expensive operations  
- Logging with proper context
- Security (no hardcoded secrets, SQL injection prevention)
- Performance considerations

## 5. Nautilus Trader Integration
- Correct use of framework patterns
- Event handling implementation
- Data adapter patterns
- Strategy lifecycle management

## 6. Positive Highlights
- Well-implemented features
- Good test coverage
- Clean code patterns
- Performance optimizations

## 7. Action Items (Prioritized)
### Critical (Must Fix)
- [List with specific line numbers]

### Important (Should Fix)
- [List with specific line numbers]

### Suggestions (Nice to Have)
- [List with specific line numbers]

Provide specific code examples for improvements where helpful.
Focus on correctness, maintainability, and adherence to the project's standards."""

    def analyze_with_claude_with_retry(
        self, 
        diff_content: str,
        file_contents: Dict[str, str],
        pr_info: Dict[str, str],
        file_categories: Dict[str, List[str]],
        max_retries: int = 3
    ) -> str:
        """Send comprehensive context to Claude with intelligent retry."""
        
        context = self._build_comprehensive_prompt(
            diff_content, file_contents, pr_info, file_categories
        )
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,  # Maximum tokens for comprehensive review
            "messages": [{"role": "user", "content": context}],
        }

        # Retry logic with progressive timeout
        for attempt in range(max_retries):
            try:
                print(f"  📡 Sending to Claude (attempt {attempt + 1}/{max_retries})...")
                
                # Progressive timeout: 180s, 240s, 300s
                timeout = 180 + (attempt * 60)
                
                response = requests.post(
                    "https://api.anthropic.com/v1/messages", 
                    headers=headers, 
                    json=payload,
                    timeout=timeout
                )

                if response.status_code == 200:
                    result = response.json()
                    return result["content"][0]["text"]
                elif response.status_code == 429:
                    wait_time = min(60, 20 * (attempt + 1))
                    print(f"  ⏳ Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    error_msg = f"API error {response.status_code}: {response.text[:200]}"
                    if attempt < max_retries - 1:
                        print(f"  ⚠️  {error_msg}, retrying...")
                        time.sleep(10)
                        continue
                    else:
                        raise Exception(error_msg)
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    print(f"  ⏱️  Timeout at {timeout}s, retrying with longer timeout...")
                    time.sleep(10)
                    continue
                else:
                    raise
            except Exception as e:
                if attempt < max_retries - 1 and "connection" in str(e).lower():
                    print(f"  🔄 Connection issue, retrying...")
                    time.sleep(15)
                    continue
                else:
                    raise

        raise Exception("Failed to get response from Claude after all retries")

    def post_review_comment(self, review_body: str) -> None:
        """Post the review as a PR comment."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        formatted_review = f"""## 🤖 Claude AI Code Review

{review_body}

---
📋 **Review Context**
- Constitution Version: 1.0.1
- Focus: TDD Compliance, Trading Logic, Type Safety
- Project: Nautilus Trader Backtesting System
- Phase: CLI Implementation (Phase 1)

*This is an automated review. For questions, check the GitHub Action logs.*
"""

        payload = {"body": formatted_review}
        url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code != 201:
            print(f"Failed to post comment: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            sys.exit(1)

        print("✅ Review posted successfully!")

    def post_error_comment(self, error_message: str) -> None:
        """Post an error comment to the PR."""
        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

            error_comment = f"""## 🤖 Claude AI Review - Error

⚠️ **Unable to complete automated review**

{error_message}

**Troubleshooting:**
1. For timeout errors: Consider breaking this into smaller PRs
2. For API errors: Check ANTHROPIC_API_KEY in repository secrets
3. For large PRs: Add 'skip-review' label to bypass

For assistance, check the GitHub Action logs or contact maintainers.
"""
            payload = {"body": error_comment}
            url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 201:
                print("✅ Error comment posted")
            else:
                print(f"❌ Failed to post error comment: {response.status_code}")

        except Exception as e:
            print(f"❌ Error posting comment: {e}")

    def run_review(self) -> None:
        """Run the complete review process."""
        try:
            print("=" * 60)
            print(f"🔍 Claude PR Reviewer v3.0.0")
            print(f"📦 Project: Nautilus Trader Backtesting System")
            print(f"🔢 PR #{self.pr_number}")
            print("=" * 60)
            
            # Validate environment
            if not self.anthropic_api_key:
                print("❌ ANTHROPIC_API_KEY is missing")
                self.post_error_comment(
                    "**Configuration Error**: ANTHROPIC_API_KEY is not set in repository secrets.\n\n"
                    "Please add it in Settings → Secrets and variables → Actions"
                )
                return

            # Get PR information
            print("📋 Fetching PR information...")
            pr_info = self.get_pr_info()
            print(f"   Title: {pr_info['title']}")
            print(f"   Author: {pr_info['user']}")
            print(f"   Branch: {pr_info['head_branch']} → {pr_info['base_branch']}")

            # Skip draft PRs unless labeled
            if pr_info.get("draft", False):
                if "review-draft" not in pr_info.get("labels", []):
                    print("⏭️  Skipping draft PR (add 'review-draft' label to force)")
                    return
                else:
                    print("   📝 Draft PR with review-draft label - proceeding")

            # Get PR diff
            print("\n📥 Fetching PR diff...")
            diff_content = self.get_pr_diff()
            diff_size = len(diff_content)
            print(f"   Size: {diff_size:,} characters")
            
            if diff_size > 100000:
                print("   ⚠️  Large diff detected - review may take longer")

            # Categorize files
            print("\n📂 Analyzing changed files...")
            file_categories = self._categorize_files()
            total_files = sum(len(files) for files in file_categories.values())
            print(f"   Total files: {total_files}")
            
            for category, files in file_categories.items():
                if files:
                    print(f"   {category}: {len(files)} files")
                    for f in files[:3]:
                        print(f"      - {f}")
                    if len(files) > 3:
                        print(f"      ... and {len(files) - 3} more")

            # Get priority files for detailed review
            print("\n📑 Fetching priority file contents...")
            priority_files = self._get_priority_files(file_categories)
            print(f"   Priority files identified: {len(priority_files)}")
            
            file_contents = self._fetch_file_contents(priority_files)
            print(f"   Successfully fetched: {len(file_contents)} files")

            # Analyze with Claude
            print("\n🧠 Sending to Claude for analysis...")
            print("   This may take 1-3 minutes for comprehensive review...")
            
            review = self.analyze_with_claude_with_retry(
                diff_content, file_contents, pr_info, file_categories
            )

            # Post review
            print("\n💬 Posting review to PR...")
            self.post_review_comment(review)

            print("\n" + "=" * 60)
            print("🎉 Review completed successfully!")
            print("=" * 60)

        except requests.exceptions.Timeout:
            print("\n❌ Request timed out after all retries")
            self.post_error_comment(
                "The review request timed out. This PR may be too large for automated review.\n\n"
                "Options:\n"
                "1. Break this into smaller, focused PRs\n"
                "2. Add 'skip-review' label to bypass automated review\n"
                "3. Request manual review from team members"
            )
            sys.exit(1)
            
        except Exception as e:
            print(f"\n❌ Error during review: {e}")
            import traceback
            traceback.print_exc()
            
            error_summary = str(e)[:300]
            self.post_error_comment(
                f"An error occurred during review:\n\n```\n{error_summary}\n```"
            )
            sys.exit(1)


def main():
    """Main entry point."""
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