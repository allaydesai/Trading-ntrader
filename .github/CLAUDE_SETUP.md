# Claude AI Code Review — Removed

The automated Claude PR review GitHub Action (`.github/workflows/claude-pr-review.yml`, job
"Claude Code Review") has been **removed pending a redesign**. It is not active and does not run
on pull requests.

- PR review is done by a separate model **outside** this CI check.
- The `ANTHROPIC_API_KEY` repository secret is no longer used by any workflow.
- The `skip-claude-review` label no longer has any effect.
- `.github/scripts/claude_reviewer.py` was the workflow's helper script. It is currently unused and
  is kept only as a reference for the redesign.

Do not follow older setup instructions for this workflow; they have been removed from this file.
