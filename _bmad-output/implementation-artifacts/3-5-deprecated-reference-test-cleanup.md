# Story 3.5: Deprecated Reference-Test Cleanup

Status: done

<!-- Trivial cleanup — Epic 3 retro action item C1. -->

## Story

As a system operator,
I want the deprecated `tests/integration/core/test_aapl_2018_reference_comparison.py` stub deleted (along with its dangling references in `deferred-work.md`),
So that the test tree no longer carries a placeholder file from a superseded story and `make test-integration` collection has one fewer module to skip.

## Background — why this story exists

Story 3.4 replaced Path A of the AAPL 2018 parity harness from an unverified legacy CSV (`data/AAPL_1min.csv`) with an IBKR autofetch path. The original CSV-based integration test (`tests/integration/core/test_aapl_2018_reference_comparison.py`) was reduced to a module-level `pytest.skip` deprecation marker rather than deleted, with a one-line note that Story 3.5 would handle the cleanup once 3.4 was stable.

3.4 has been `done` since 2026-05-09 (code review applied 8 patches, all quality gates clean). The placeholder is no longer load-bearing. This story removes it and the two `deferred-work.md` entries that pointed at it (C1, C2 from the 3-3 review batch).

## Scope & Non-Goals

**In scope:**

- Delete `tests/integration/core/test_aapl_2018_reference_comparison.py` (the 21-line module-level skip stub, see file content for shape).
- Remove the two `deferred-work.md` entries that reference the deleted file by line number: **C1** (`legacy_catalog_service._rebuild_availability_cache()` private API call at line 236) and **C2** (`chmod 0o500` permission test at lines 1681–1703). Both entries pre-date the file becoming a stub; their referenced code no longer exists.
- Verify `scripts/verify_aapl_2018_reference.py:46` still has a meaningful comment after the deleted file is gone — the docstring there says `tests/integration/core/test_aapl_2018_reference_comparison.py is the` (truncated by my probe). Update the comment to reference `test_aapl_2018_ibkr_vs_firstrate.py` (the live test) instead.
- Run the full quality suite (`make format && make lint && make typecheck && make test-unit && make test-component && make test-integration`) to confirm no collateral references break.

**Out of scope:**

- Any change to `test_aapl_2018_ibkr_vs_firstrate.py` (the live Story 3.4 test). It stays.
- Any change to `scripts/verify_aapl_2018_reference.py` beyond the one-line comment update (the script's IBKR autofetch path is the production verification harness).
- Re-organising the integration test directory or updating any imports — this is a single-file delete plus two small docs edits.
- Resolving any other Story 3.4 deferred-work entries (D1–D10). Those are separate concerns.

## Acceptance Criteria

1. **File deleted** — `tests/integration/core/test_aapl_2018_reference_comparison.py` is removed from the working tree. `git status` shows the file as deleted (or it's already absent on `main` after merge).

2. **deferred-work.md entries removed** — The C1 and C2 entries under `## Deferred from: code review of 3-3-backtest-verification-and-reference-comparison (2026-05-03)` that reference the deleted file are removed. Other entries in that section (C3–C11) stay. The section header stays.

3. **Harness comment refreshed** — `scripts/verify_aapl_2018_reference.py` no longer contains a docstring or comment line referencing the deleted test path. If a comment reference exists at line ~46, it's replaced with a reference to `tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py`.

4. **Quality gates clean** — `make format && make lint && make typecheck` clean. `make test-unit && make test-component` clean (zero regressions vs Story 3.4's 851u + 621c baseline). `make test-integration` runs to completion with no new collection error or import failure attributable to this change. Pre-existing flakiness count (15-ish failures from Nautilus C-extension parallelism) may not improve from this story alone — that's Story 3-X / action item C8 territory.

5. **No dangling references** — `grep -rn "test_aapl_2018_reference_comparison" src/ tests/ scripts/ Makefile pyproject.toml` returns no results outside `_bmad-output/implementation-artifacts/` (the historical story files there are immutable historical record).

## Tasks / Subtasks

- [x] Task 1: Delete the stub and verify no live references (AC: #1, #5)
  - [x] 1.1 `git rm tests/integration/core/test_aapl_2018_reference_comparison.py`
  - [x] 1.2 `grep -rn "test_aapl_2018_reference_comparison" src/ tests/ scripts/ Makefile pyproject.toml` — confirm zero live-code matches.
  - [x] 1.3 If any live-code match surfaces, surface in Review Findings before proceeding (no live caller is expected).

- [x] Task 2: Update `deferred-work.md` (AC: #2)
  - [x] 2.1 Remove the C1 and C2 entries from the `## Deferred from: code review of 3-3-...` section.
  - [x] 2.2 Confirm C3–C11 in that section are untouched.
  - [x] 2.3 Confirm the file header and section structure are preserved exactly.

- [x] Task 3: Update `scripts/verify_aapl_2018_reference.py` docstring (AC: #3)
  - [x] 3.1 Read the existing docstring/comment at the live reference site.
  - [x] 3.2 Replace `test_aapl_2018_reference_comparison.py` with `test_aapl_2018_ibkr_vs_firstrate.py` in any prose reference. If the original sentence framed the deleted file as the "integration test entrypoint", reframe to point at the live test.
  - [x] 3.3 Verify the script still imports and runs (`uv run python scripts/verify_aapl_2018_reference.py --help` exits 0).

- [x] Task 4: Quality gates (AC: #4)
  - [x] 4.1 `make format && make lint && make typecheck` — clean.
  - [x] 4.2 `make test-unit` — zero regressions (851 baseline).
  - [x] 4.3 `make test-component` — zero regressions (621 baseline).
  - [x] 4.4 `make test-integration` — runs to completion. Confirm Story 3.4's `test_aapl_2018_ibkr_vs_firstrate.py` is collected (3 tests, 1 design-mandated FAIL per AC #6, 2 PASS) when `IBKR_AVAILABLE=1 E2E_CATALOG_AVAILABLE=1` is set; skips cleanly when not. Note any new failures inline.

## Dev Notes

### Architecture Compliance

- **No production-code changes.** Story 3.5 is test-tree + docs cleanup only. No `src/` edits.
- **Single-PR blast radius.** This story should be one commit, ≤ 30 LoC of changes including the file deletion. If the diff grows beyond that, scope has crept — pause and flag.

### Existing State (verified 2026-05-09)

- `tests/integration/core/test_aapl_2018_reference_comparison.py` is a 21-line module-level `pytest.skip` stub. Reproduced for context:

```python
"""DEPRECATED (Story 3.4): superseded by ``test_aapl_2018_ibkr_vs_firstrate.py``.
... (10-line docstring)
"""

from __future__ import annotations

import pytest

pytest.skip(
    "DEPRECATED — see tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py "
    "(Story 3.4). Will be removed in Story 3.5 cleanup.",
    allow_module_level=True,
)
```

- `deferred-work.md:97-98` contains two entries that reference the deleted file by line number; both are no-ops once the file is gone.
- `scripts/verify_aapl_2018_reference.py:46` mentions the deleted path in a docstring/comment line.

### Anti-patterns to Avoid

- **Don't grow this story.** No "while we're here" refactors of the 3.4 IBKR test, no consolidation of integration test fixtures, no additional deferred-work entries dropped without retro/review approval.
- **Don't delete `test_aapl_2018_ibkr_vs_firstrate.py`** — that's the live parity harness. Read the path twice before any `rm`.
- **Don't touch `scripts/verify_aapl_2018_reference.py` beyond the comment refresh.** It's the production verification entrypoint.

### Testing Requirements

- No new tests written — this story removes a (skipped) test and changes no behavior. Quality gates verify nothing else broke.

### References

- [Source: `_bmad-output/implementation-artifacts/3-4-ibkr-vs-firstrate-parity-comparison.md` Tasks 3.4, 3-x] — created the deprecation marker, deferred this cleanup to 3.5.
- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-09.md` action C1] — retrospective decision driving this story.
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md` C1, C2 entries under 3-3 review batch] — entries to remove in Task 2.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7 (1M context) via bmad-dev-story workflow.

### Debug Log References

- `make format` — 300 files left unchanged.
- `make lint` — All checks passed.
- `make typecheck` — Success: no issues found in 56 source files.
- `make test-unit` — 851 passed (matches 3.4 baseline).
- `make test-component` — 617 passed, 16 skipped. The 4-test drift from the 621 baseline is from prior commit `774090d` (`refactor(backtest): remove unreachable execute_multi`); this story touches no component-test code, so its delta is zero.
- `make test-integration` — 129 passed, 14 failed (signal-5 crashes from pre-existing Nautilus C-extension parallelism, Epic 3 retro action C8; baseline was ~15), 2 skipped. Story 3.4's `test_aapl_2018_ibkr_vs_firstrate.py` was collected and skipped cleanly because `IBKR_AVAILABLE=1` is not set in this environment. Deleted stub no longer appears in collection.
- `grep -rn "test_aapl_2018_reference_comparison" src/ tests/ scripts/ Makefile pyproject.toml` returns zero live-code matches (AC #5).

### Completion Notes List

- Deleted `tests/integration/core/test_aapl_2018_reference_comparison.py` via `git rm` (21-line module-level skip stub from Story 3.4).
- Removed C1 and C2 entries from the `## Deferred from: code review of 3-3-backtest-verification-and-reference-comparison (2026-05-03)` section of `deferred-work.md`. C3–C11 untouched. Section header preserved.
- Replaced the closing paragraph of the `scripts/verify_aapl_2018_reference.py` module docstring (the prose reference to the deprecated CSV-based test) with a one-line pointer to `tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py`. No code changes; script `--help` invocation works under `PYTHONPATH=.` as before (the bare `uv run python scripts/...` path-resolution issue is pre-existing and out of scope).
- Final diff: 3 files touched (1 deletion, 2 edits), well within the ≤30 LoC scope guardrail.

### File List

- `tests/integration/core/test_aapl_2018_reference_comparison.py` — deleted
- `_bmad-output/implementation-artifacts/deferred-work.md` — modified (removed C1, C2)
- `scripts/verify_aapl_2018_reference.py` — modified (docstring update, no code changes)

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-05-09 | Bob (SM) | Story 3.5 created via `bmad-create-story 3-5`. Status: backlog → ready-for-dev. Single-PR cleanup of Story 3.4's deprecation marker. |
| 2026-05-10 | Amelia (Dev) | Implemented all 4 tasks. Stub deleted, deferred-work.md C1/C2 removed, verify script docstring refreshed. Quality gates clean (zero regressions vs 3.4 baseline). Status: ready-for-dev → review. |
