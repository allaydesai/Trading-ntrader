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
from typing import Dict, List, Optional
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
            "implementation": [],
            "config": [],
            "docs": [],
        }

        for file_path in self.changed_files:
            if "test" in file_path or "tests" in file_path:
                categories["tests"].append(file_path)
            elif file_path.endswith(".py"):
                categories["implementation"].append(file_path)
            elif any(cfg in file_path for cfg in [".yaml", ".yml", ".env", "config"]):
                categories["config"].append(file_path)
            elif any(doc in file_path for doc in [".md", "docs", "README"]):
                categories["docs"].append(file_path)

        return {k: v for k, v in categories.items() if v}

    def _build_concise_prompt(
        self,
        diff_content: str,
        pr_info: Dict[str, str],
        file_categories: Dict[str, List[str]],
    ) -> str:
        """Build a more concise review prompt to avoid timeouts."""

        # Truncate diff if too large (keep first and last parts)
        max_diff_size = 30000  # ~30KB
        if len(diff_content) > max_diff_size:
            half_size = max_diff_size // 2
            diff_content = (
                diff_content[:half_size]
                + "\n\n... [DIFF TRUNCATED - LARGE PR] ...\n\n"
                + diff_content[-half_size:]
            )

        return f"""You are reviewing a PR for a Nautilus Trader Backtesting System (Python 3.11+, TDD mandatory).

# CRITICAL REQUIREMENTS
1. **TDD**: Tests MUST exist before implementation (80% coverage minimum)
2. **Code Limits**: Files <500 lines, functions <50 lines, classes <100 lines
3. **Type Safety**: All functions need type hints, mypy must pass
4. **Dependencies**: Only via UV commands (never edit pyproject.toml)

# PROJECT CONTEXT
- Trading backtesting system using Nautilus Trader
- IBKR data integration (50 req/sec limit)
- Tech: nautilus_trader[ib], FastAPI, Pydantic v2, PostgreSQL+TimescaleDB
- Current Phase: CLI implementation

# TRADING ENTITIES
- TradingStrategy (SMA, mean reversion, momentum)
- MarketData (OHLCV bars)
- Trade (entry/exit, PnL)
- Portfolio (positions, cash)
- BacktestResult (metrics: Sharpe, drawdown, win rate)

# KEY CHECKS
- Position sizing: 1% risk default, 10% max
- Commissions: $0.005/share or percentage
- Slippage: 1 basis point
- Test files exist for all implementations
- No hardcoded secrets

# PR INFO
Title: {pr_info["title"]}
Author: {pr_info["user"]}
Branch: {pr_info["head_branch"]} → {pr_info["base_branch"]}

# FILES CHANGED
{json.dumps(file_categories, indent=2)}

# DIFF
```diff
{diff_content}
```

Review this PR focusing on:
1. TDD compliance (tests before code?)
2. Trading logic correctness
3. Type safety and code structure limits
4. Critical bugs or security issues

Provide actionable feedback with specific line numbers.
Be concise but thorough."""

    def analyze_with_claude_with_retry(
        self, diff_content: str, pr_info: Dict[str, str], max_retries: int = 3
    ) -> str:
        """Send the diff to Claude with retry logic."""

        file_categories = self._categorize_files()

        # Use concise prompt to reduce tokens and processing time
        context = self._build_concise_prompt(diff_content, pr_info, file_categories)

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 3000,  # Reduced from 4000
            "messages": [{"role": "user", "content": context}],
        }

        # Retry logic with exponential backoff
        for attempt in range(max_retries):
            try:
                print(
                    f"  📡 Sending request to Claude (attempt {attempt + 1}/{max_retries})..."
                )

                # Increased timeout: 120 seconds base + 60 seconds per retry
                timeout = 120 + (attempt * 60)

                response = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code == 200:
                    result = response.json()
                    return result["content"][0]["text"]
                elif response.status_code == 429:  # Rate limit
                    wait_time = 30 * (attempt + 1)
                    print(f"  ⏳ Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise Exception(
                        f"Claude API error: {response.status_code} - {response.text}"
                    )

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = 10 * (attempt + 1)
                    print(f"  ⏱️  Timeout occurred, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  ⚠️  Error: {str(e)[:100]}, retrying...")
                    time.sleep(5)
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

        # Create a formatted review comment
        formatted_review = f"""## 🤖 Claude AI Code Review

{review_body}

---
📋 **Review Context**
- Focus: TDD Compliance & Trading Logic
- Project: Nautilus Trader Backtesting System
- Phase: CLI Implementation

*Generated by Claude AI. Check Action logs for details.*
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

**Common Issues:**
- Large PR causing timeout (try reducing PR size)
- API key issues (verify ANTHROPIC_API_KEY in secrets)
- Rate limiting (wait and retry)

Check GitHub Action logs for details.
"""
            payload = {"body": error_comment}
            url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code == 201:
                print("✅ Error comment posted")
            else:
                print(f"❌ Failed to post error comment: {response.status_code}")

        except Exception as e:
            print(f"❌ Failed to post error comment: {e}")

    def run_review(self) -> None:
        """Run the complete review process."""
        try:
            print(f"🔍 Starting Claude review for PR #{self.pr_number}")
            print(f"📁 Changed files: {len(self.changed_files)} files")

            # Validate environment
            if not self.anthropic_api_key:
                print("❌ ANTHROPIC_API_KEY is required but not set")
                self.post_error_comment(
                    "ANTHROPIC_API_KEY not configured in repository secrets"
                )
                return

            # Get PR information
            print("📋 Fetching PR details...")
            pr_info = self.get_pr_info()

            # Skip draft PRs unless labeled
            if pr_info.get("draft", False) and "review-draft" not in pr_info.get(
                "labels", []
            ):
                print("⏭️  Skipping draft PR")
                return

            # Get PR diff
            print("📥 Fetching PR diff...")
            diff_content = self.get_pr_diff()

            diff_size = len(diff_content)
            print(f"📊 Diff size: {diff_size:,} characters")

            # Warn if very large
            if diff_size > 50000:
                print("⚠️  Large PR detected - review may be limited")

            # Analyze with Claude (with retry logic)
            print("🧠 Analyzing with Claude...")
            review = self.analyze_with_claude_with_retry(diff_content, pr_info)

            # Post review
            print("💬 Posting review comment...")
            self.post_review_comment(review)

            print("🎉 Review completed successfully!")

        except requests.exceptions.Timeout:
            print("❌ Request timeout after retries")
            self.post_error_comment(
                "Review timed out. PR may be too large. Consider:\n"
                "- Breaking into smaller PRs\n"
                "- Adding 'skip-review' label for this PR"
            )
            sys.exit(1)

        except Exception as e:
            print(f"❌ Error: {e}")
            self.post_error_comment(f"Error: {str(e)[:200]}")
            sys.exit(1)


def main():
    """Main entry point."""
    print("🚀 Claude PR Reviewer v2.1.0")
    print("🏗️  Nautilus Trader Backtesting System")

    try:
        reviewer = ClaudePRReviewer()
        reviewer.run_review()
    except KeyboardInterrupt:
        print("\n⚠️  Cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
