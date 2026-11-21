# GitHub Actions Configuration

## Workflows

- `ci.yml`: the **fast lane**. Runs on every PR and every push to `main`/`develop`, with a target
  under 8 minutes. Jobs: ruff lint, then the unit, component and integration (`--forked`) tiers
  in parallel on Python 3.11. A coverage job combines each tier's coverage data file and enforces
  ≥64%. It never re-runs tests. A newer push to the same PR cancels the run in flight. Changes
  that touch only docs or planning files (`**.md`, `docs/`, `_bmad*/`, `archive/`) skip CI.
- `nightly.yml`: the **slow lane**. Runs daily at 07:00 UTC and on demand (*Run workflow*). It runs
  the full suite on Python 3.12 and two advisory checks: mypy, and bandit plus pip-audit.

Run nightly by hand before a release or after a dependency bump.

## Flaky tests: quarantine

A known-flaky test gets `@pytest.mark.quarantine(reason="...")`. The PR lane skips it
(`-m "not quarantine"`), and nightly runs it five times in an advisory job, so you can see
when it is fixed. `tests/unit/governance/test_quarantine.py` requires a reason on every
entry and caps the list at 5. Fix one before adding another. To un-quarantine a test,
fix it, watch it pass nightly, then delete the marker.

## Code review

PR review is done by CodeRabbit, configured in [`/.coderabbit.yaml`](../../.coderabbit.yaml).
It reads `CLAUDE.md`, `_bmad-output/project-context.md` and
`docs/governance/development-principles.md` as review guidelines.

The old `claude-pr-review.yml` workflow was removed. See [`.github/CLAUDE_SETUP.md`](../CLAUDE_SETUP.md).
