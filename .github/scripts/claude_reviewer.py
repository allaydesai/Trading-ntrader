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
from typing import Dict
from dataclasses import dataclass


@dataclass
class ReviewComment:
    """Represents a code review comment."""

    path: str
    line: int
    body: str
    severity: str = "info"  # info, warning, error


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
        required_vars = [
            "ANTHROPIC_API_KEY",
            "GITHUB_TOKEN",
            "REPO_OWNER",
            "REPO_NAME",
            "BASE_SHA",
            "HEAD_SHA",
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")

        self.github_api_base = (
            f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}"
        )

    def get_pr_diff(self) -> str:
        """Fetch the complete diff for the PR."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3.diff",
        }

        url = f"{self.github_api_base}/pulls/{self.pr_number}"
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch PR diff: {response.status_code} - {response.text}"
            )

        return response.text

    def get_file_content(self, file_path: str, sha: str) -> str:
        """Get the content of a file at a specific commit."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3.raw",
        }

        url = f"{self.github_api_base}/contents/{file_path}?ref={sha}"
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            return response.text
        else:
            return f"Could not fetch file content: {response.status_code}"

    def analyze_with_claude(
        self, diff_content: str, file_contents: Dict[str, str]
    ) -> str:
        """Send the diff to Claude for analysis."""

        # Create comprehensive context for Claude
        context = f"""
You are an expert Python code reviewer specializing in trading systems and financial software.
You are reviewing a Pull Request for a Nautilus Trader backtesting system.

IMPORTANT CONTEXT:
- This is a Python backend project using Nautilus Trader framework
- Tech stack: Python 3.11+, nautilus_trader, FastAPI, Pydantic, pytest
- Architecture: TDD (Test-Driven Development) is NON-NEGOTIABLE
- Code style: Follow PEP8, max line length 100 chars, type hints required
- Testing: pytest with 80%+ coverage required

REVIEW FOCUS AREAS:
1. **Code Quality**: Type hints, docstrings, error handling, performance
2. **Testing**: Are tests comprehensive? Do they follow TDD principles?
3. **Architecture**: Does code follow SOLID principles? Is it maintainable?
4. **Trading Logic**: Is the trading strategy implementation sound?
5. **Nautilus Trader Best Practices**: Proper use of framework patterns
6. **Security**: No hardcoded secrets, proper validation
7. **Performance**: Efficient algorithms, proper async/await usage

REVIEW STYLE:
- Be constructive and specific
- Suggest improvements with code examples when helpful
- Point out both positives and areas for improvement
- Focus on critical issues first
- Consider the trading domain context

CHANGED FILES: {", ".join(self.changed_files)}

FILE CONTENTS:
{json.dumps(file_contents, indent=2)}

PULL REQUEST DIFF:
{diff_content}

Please provide a thorough code review focusing on the areas above.
Format your response as a structured review with:
1. Overall assessment
2. Specific file-by-file feedback
3. Critical issues (if any)
4. Suggestions for improvement
5. Praise for good practices

Keep the review professional, constructive, and actionable.
"""

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": "claude-3-sonnet-20240229",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": context}],
        }

        response = requests.post(
            "https://api.anthropic.com/v1/messages", headers=headers, json=payload
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
*This review was generated automatically by Claude AI. For questions about this review, please check the GitHub Action logs or contact the maintainers.*
"""

        payload = {"body": formatted_review}

        url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
        response = requests.post(url, headers=headers, json=payload)

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

{error_message}

Please check the repository settings and GitHub Action configuration.
"""
            payload = {"body": error_comment}
            url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
            response = requests.post(url, headers=headers, json=payload)

            if response.status_code == 201:
                print("✅ Error comment posted successfully")
            else:
                print(f"❌ Failed to post error comment: {response.status_code}")

        except Exception as e:
            print(f"❌ Failed to post error comment: {e}")

    def run_review(self) -> None:
        """Run the complete review process."""
        try:
            print(f"🔍 Starting Claude review for PR #{self.pr_number}")
            print(f"📁 Changed files: {', '.join(self.changed_files)}")
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
                    "ANTHROPIC_API_KEY secret is not configured in repository settings"
                )
                return

            # Get PR diff
            print("📥 Fetching PR diff...")
            diff_content = self.get_pr_diff()

            # Get file contents for better context
            print("📄 Fetching file contents...")
            file_contents = {}
            for file_path in self.changed_files[
                :10
            ]:  # Limit to 10 files to avoid token limits
                try:
                    content = self.get_file_content(file_path, self.head_sha)
                    file_contents[file_path] = content[:5000]  # Limit content size
                except Exception as e:
                    print(f"⚠️  Could not fetch content for {file_path}: {e}")
                    file_contents[file_path] = "Content unavailable"

            # Analyze with Claude
            print("🧠 Analyzing with Claude...")
            review = self.analyze_with_claude(diff_content, file_contents)

            # Post review
            print("💬 Posting review comment...")
            self.post_review_comment(review)

            print("🎉 Review process completed successfully!")

        except Exception as e:
            print(f"❌ Error during review: {e}")
            # Post error comment
            error_comment = f"""## 🤖 Claude AI Review Error

An error occurred while generating the automated review:

```
{str(e)}
```

Please check the GitHub Action logs for more details.
"""
            try:
                headers = {
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                }
                payload = {"body": error_comment}
                url = f"{self.github_api_base}/issues/{self.pr_number}/comments"
                requests.post(url, headers=headers, json=payload)
            except Exception:
                pass
            sys.exit(1)


def main():
    """Main entry point."""
    print("🚀 Claude PR Reviewer starting...")

    reviewer = ClaudePRReviewer()
    reviewer.run_review()


if __name__ == "__main__":
    main()
