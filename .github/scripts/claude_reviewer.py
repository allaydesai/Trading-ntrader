#!/usr/bin/env python3
"""
Claude PR Reviewer v4 — Structured diff analysis with critical review focus.

Key improvements over v3:
- Parses diff into annotated per-file blocks with real line numbers
- Strips static context bloat (relies on CLAUDE.md instead)
- Prompt designed for critical issue-finding, not praise
- Anti-hallucination guardrails (must cite real line numbers from diff)
- 8192 max_tokens for thorough output
"""

import os
import re
import sys
import time
import requests
from dataclasses import dataclass, field


@dataclass
class FileDiff:
    """Parsed diff for a single file with real line numbers."""

    path: str
    added_lines: int = 0
    removed_lines: int = 0
    hunks: list = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False


class ClaudePRReviewer:
    """Claude-powered PR reviewer with structured diff analysis."""

    def __init__(self):
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.pr_number = int(os.getenv("PR_NUMBER", "0"))
        self.repo_owner = os.getenv("REPO_OWNER")
        self.repo_name = os.getenv("REPO_NAME")
        self.base_sha = os.getenv("BASE_SHA")
        self.head_sha = os.getenv("HEAD_SHA")
        self.changed_files = os.getenv("CHANGED_FILES", "").split()
        self.claude_context_loaded = False

        self._validate_env()
        self.github_api = (
            f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}"
        )

    def _validate_env(self):
        required = {
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "GITHUB_TOKEN": self.github_token,
            "REPO_OWNER": self.repo_owner,
            "REPO_NAME": self.repo_name,
            "BASE_SHA": self.base_sha,
            "HEAD_SHA": self.head_sha,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"Missing env vars: {missing}")

    # ── GitHub helpers ──────────────────────────────────────────────

    def _gh_get(self, path, accept="application/vnd.github.v3+json", timeout=30):
        headers = {"Authorization": f"token {self.github_token}", "Accept": accept}
        return requests.get(f"{self.github_api}/{path}", headers=headers, timeout=timeout)

    def get_pr_info(self):
        resp = self._gh_get(f"pulls/{self.pr_number}")
        resp.raise_for_status()
        d = resp.json()
        return {
            "title": d.get("title", ""),
            "body": d.get("body", "") or "",
            "user": d.get("user", {}).get("login", "unknown"),
            "base_branch": d.get("base", {}).get("ref", "main"),
            "head_branch": d.get("head", {}).get("ref", ""),
            "draft": d.get("draft", False),
            "labels": [lb["name"] for lb in d.get("labels", [])],
        }

    def get_pr_diff(self):
        resp = self._gh_get(
            f"pulls/{self.pr_number}", accept="application/vnd.github.v3.diff"
        )
        resp.raise_for_status()
        return resp.text

    def get_claude_context(self):
        for path in ["CLAUDE.md", ".github/CLAUDE.md"]:
            try:
                resp = self._gh_get(
                    f"contents/{path}?ref={self.head_sha}",
                    accept="application/vnd.github.v3.raw",
                    timeout=10,
                )
                if resp.status_code == 200:
                    content = resp.text
                    if len(content) > 12000:
                        lines = content.split("\n")
                        content = (
                            "\n".join(lines[:120])
                            + "\n\n[... truncated ...]\n\n"
                            + "\n".join(lines[-40:])
                        )
                    self.claude_context_loaded = True
                    return content
            except Exception:
                pass
        return ""

    # ── Diff parsing ────────────────────────────────────────────────

    def _parse_diff(self, raw_diff):
        """Parse unified diff into structured per-file blocks with real line numbers."""
        files = []
        current = None
        hunk_lines = []
        new_ln = 0

        for line in raw_diff.split("\n"):
            # ── new file boundary ──
            if line.startswith("diff --git"):
                if current and hunk_lines:
                    current.hunks.append("\n".join(hunk_lines))
                    hunk_lines = []
                m = re.search(r"diff --git a/.* b/(.*)", line)
                current = FileDiff(path=m.group(1) if m else "unknown")
                files.append(current)
                continue

            if not current:
                continue

            if line.startswith("new file"):
                current.is_new = True
            elif line.startswith("deleted file"):
                current.is_deleted = True
            elif line.startswith("Binary files"):
                current.is_binary = True

            # ── hunk header ──
            hunk_m = re.match(
                r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line
            )
            if hunk_m:
                if hunk_lines:
                    current.hunks.append("\n".join(hunk_lines))
                new_ln = int(hunk_m.group(1))
                ctx = hunk_m.group(2).strip()
                hunk_lines = [f"@@ line {new_ln}: {ctx}" if ctx else f"@@ line {new_ln}"]
                continue

            # ── diff content ──
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("\\ No newline"):
                continue

            if line.startswith("+"):
                current.added_lines += 1
                hunk_lines.append(f"  {new_ln:>4} + {line[1:]}")
                new_ln += 1
            elif line.startswith("-"):
                current.removed_lines += 1
                hunk_lines.append(f"       - {line[1:]}")
            elif line.startswith(" "):
                hunk_lines.append(f"  {new_ln:>4}   {line[1:]}")
                new_ln += 1

        if current and hunk_lines:
            current.hunks.append("\n".join(hunk_lines))

        return files

    def _format_diff(self, file_diffs):
        """Format parsed diffs into annotated text with real line numbers."""
        sections = []
        for fd in file_diffs:
            if fd.is_binary:
                sections.append(f"=== {fd.path} [BINARY] ===")
                continue
            tag = ""
            if fd.is_new:
                tag = " [NEW]"
            elif fd.is_deleted:
                tag = " [DELETED]"
            header = f"=== {fd.path}{tag} (+{fd.added_lines}/-{fd.removed_lines}) ==="
            body = "\n\n".join(fd.hunks)
            sections.append(f"{header}\n{body}")
        return "\n\n".join(sections)

    # ── Prompt ──────────────────────────────────────────────────────

    def _build_prompt(self, annotated_diff, pr_info, claude_context, file_diffs):
        py_files = [f for f in file_diffs if f.path.endswith(".py")]
        impl_files = [f.path for f in py_files if "/test" not in f.path]
        test_files = [f.path for f in py_files if "/test" in f.path]

        file_list = "Implementation files:\n"
        for p in impl_files:
            file_list += f"  {p}\n"
        file_list += f"\nTest files:\n"
        for p in test_files:
            file_list += f"  {p}\n"

        # Truncate diff only if massive
        truncated = len(annotated_diff) > 100000
        if truncated:
            annotated_diff = (
                annotated_diff[:95000]
                + "\n\n[... DIFF TRUNCATED — remaining files omitted ...]\n"
            )

        return f"""You are a senior code reviewer. Your job is to find REAL problems in this PR.

STRICT RULES — VIOLATIONS WILL MAKE THE REVIEW USELESS:

1. ONLY cite line numbers that appear in the diff below. Lines are prefixed like "  42 + code" (line 42, added) or "  42   code" (line 42, context). Reference these as "file.py:42". NEVER invent line numbers.
2. QUOTE the actual code when flagging an issue — copy the exact text from the diff.
3. If a section has NO real issues, write "No issues found." and move on. Do NOT manufacture problems or pad with praise.
4. Focus on ADDED and MODIFIED code (lines marked with +). Context lines are for understanding only.
5. Do NOT give star ratings, overall scores, or "EXCELLENT" assessments. Be neutral and factual.
6. Do NOT compliment code. Only describe problems and their fixes.

PROJECT RULES (from CLAUDE.md):
{claude_context if claude_context else "(CLAUDE.md not available)"}

PR: {pr_info["title"]}
Branch: {pr_info["head_branch"]} -> {pr_info["base_branch"]}
Description:
{pr_info["body"][:3000] if pr_info["body"] else "(none)"}

{file_list}

ANNOTATED DIFF {"(truncated)" if truncated else "(complete)"}:
Lines prefixed with + are additions, - are deletions, no prefix = context.
Numbers on the left are REAL line numbers in the new file version.

{annotated_diff}

REVIEW SECTIONS (use exactly these headers):

## 1. Bugs & Correctness
Logic errors, off-by-one, null/None mishandling, wrong variable, swallowed exceptions, resource leaks, race conditions. For each issue: cite file:line, quote the code, explain the bug, suggest fix.

## 2. Security & Credentials
Hardcoded secrets, credentials in logs/errors, injection risks, missing input validation at system boundaries.

## 3. Test Coverage Gaps
For each implementation file, verify a test file exists. Flag untested public methods, untested error/edge-case paths, assertions that don't verify behavior (e.g. just checking no exception was raised). Check test isolation — are tests independent or do they share mutable state?

## 4. Design & Maintainability
Functions > 50 lines, files > 500 lines, missing type hints on public APIs, cyclomatic complexity, duplicated logic, tight coupling between modules.

## 5. Action Items
Summarize ALL findings into:
- **Must Fix** — bugs, security issues, correctness problems
- **Should Fix** — missing tests, error handling gaps, maintainability concerns
- **Consider** — minor improvements, style suggestions

If the PR is genuinely clean, say so briefly without effusive praise."""

    # ── Claude API ──────────────────────────────────────────────────

    def _call_claude(self, prompt, max_retries=3):
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }

        for attempt in range(max_retries):
            try:
                timeout = 180 + (attempt * 60)
                print(f"  Claude API call (attempt {attempt + 1}, timeout {timeout}s)")

                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if resp.status_code == 200:
                    return resp.json()["content"][0]["text"]
                elif resp.status_code == 429:
                    wait = min(60, 20 * (attempt + 1))
                    print(f"  Rate limited, waiting {wait}s")
                    time.sleep(wait)
                elif attempt < max_retries - 1:
                    print(f"  Error {resp.status_code}, retrying")
                    time.sleep(10)
                else:
                    raise Exception(
                        f"API error {resp.status_code}: {resp.text[:200]}"
                    )
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    print("  Timeout, retrying")
                    time.sleep(10)
                else:
                    raise

        raise Exception("All retries exhausted")

    # ── GitHub posting ──────────────────────────────────────────────

    def _post_comment(self, body):
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        resp = requests.post(
            f"{self.github_api}/issues/{self.pr_number}/comments",
            headers=headers,
            json={"body": body},
            timeout=30,
        )
        if resp.status_code != 201:
            print(f"Failed to post comment: {resp.status_code} {resp.text[:300]}")
            sys.exit(1)

    # ── Main flow ───────────────────────────────────────────────────

    def run_review(self):
        try:
            print(f"=== Claude PR Reviewer v4.0 | PR #{self.pr_number} ===")

            if not self.anthropic_api_key:
                self._post_comment("## Review Error\nANTHROPIC_API_KEY not configured.")
                return

            pr_info = self.get_pr_info()
            print(f"PR: {pr_info['title']}")
            print(f"   {pr_info['head_branch']} -> {pr_info['base_branch']}")

            if pr_info.get("draft") and "review-draft" not in pr_info.get("labels", []):
                print("Skipping draft PR")
                return

            # Fetch and parse diff
            print("Fetching diff...")
            raw_diff = self.get_pr_diff()
            print(f"  {len(raw_diff):,} chars")

            print("Parsing diff...")
            file_diffs = self._parse_diff(raw_diff)
            non_binary = [f for f in file_diffs if not f.is_binary]
            print(f"  {len(file_diffs)} files ({len(non_binary)} with content)")
            for fd in file_diffs[:15]:
                print(f"    {fd.path} +{fd.added_lines}/-{fd.removed_lines}")
            if len(file_diffs) > 15:
                print(f"    ... and {len(file_diffs) - 15} more")

            annotated_diff = self._format_diff(file_diffs)
            print(f"  Annotated: {len(annotated_diff):,} chars")

            # Fetch project context
            claude_ctx = self.get_claude_context()
            print(f"  CLAUDE.md: {'loaded' if self.claude_context_loaded else 'not found'}")

            # Build prompt and review
            prompt = self._build_prompt(
                annotated_diff, pr_info, claude_ctx, file_diffs
            )
            print(f"  Prompt: {len(prompt):,} chars")

            print("Analyzing...")
            review = self._call_claude(prompt)

            # Post
            print("Posting review...")
            footer = (
                f"---\n*Automated review"
                f" | CLAUDE.md {'loaded' if self.claude_context_loaded else 'not found'}"
                f" | {len(file_diffs)} files analyzed*"
            )
            self._post_comment(f"## Code Review\n\n{review}\n\n{footer}")
            print("Done")

        except Exception as e:
            print(f"Error: {e}")
            import traceback

            traceback.print_exc()
            self._post_comment(
                f"## Review Error\n\n```\n{str(e)[:300]}\n```"
            )
            sys.exit(1)


def main():
    try:
        ClaudePRReviewer().run_review()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"Fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
