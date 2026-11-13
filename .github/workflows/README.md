# GitHub Actions Configuration

This directory contains the GitHub Actions workflows for automated CI.

## Workflows

- `ci.yml` — the project's CI pipeline.

## Removed: Claude PR Review

The `claude-pr-review.yml` workflow ("Claude Code Review") has been **removed pending a
redesign**. It is no longer active, no longer runs on pull requests, and no longer needs the
`ANTHROPIC_API_KEY` repository secret. The `skip-claude-review` label has no effect.

PR review is done by a separate model outside this CI check. See
[`.github/CLAUDE_SETUP.md`](../CLAUDE_SETUP.md) for the status note.
