# Archive — Phase 2 (ETFs)

Snapshot of the completed Phase 2 BMAD cycle — FirstRate ETF Import (FMP Metadata
Loader). Closed 2026-07-26 with Epic 5 and the venue-gate closure (100% coverage,
0 unresolved tickers; see [Phase 2 venue gate CLOSED] in project memory).

## Planning artifacts

These are the planning artifacts as they stood at completion. They were moved out of
`_bmad-output/planning-artifacts/` so the next effort can regenerate `prd.md` /
`epics.md` / `architecture.md` on a clean slate without overwriting Phase 2.

- `prd.md` — Phase 2 PRD
- `epics.md` — Phase 2 epic/story breakdown (5 epics, 29 stories)
- `architecture.md` — Phase 2 solution architecture
- `implementation-readiness-report-2026-06-17.md` — readiness gate

## Implementation history

`implementation-artifacts/` — the permanent Phase 2 build record: story files
(`1-1`…`5-5`), `epic-1-retro-2026-06-20.md` (epics 2–5 retros were optional and not
taken), `deferred-work.md`, `sprint-status.yaml`, and the `4-6-evidence/` and
`venue-backfill-evidence/` evidence directories. Moved here 2026-07-26 because Phase 2
reused Phase 1's `1-x`…`4-x` epic/story numbering (Phase 2 added `5-x`); keeping the
completed cycle in the live `_bmad-output/implementation-artifacts/` folder would
collide with the next effort's story numbering the same way Phase 1 did.

`deferred-work.md` carried two open (non-blocking) items at close: the
`DataCatalogService` per-request cache-scan cost, and a 961-line integration test file
over the 500-line guideline. Both were explicitly deferred to `main` as follow-ups, not
resolved — check `deferred-work.md` before assuming they're closed.

## Harness runs

`harness-runs/` — leftover artifacts from an external automation harness run against
Epic 2 (`harness-story-2.1/2.2/2.3-spec.md`) and Epic 3 (`review_story31/32/33.diff`,
originally dotfiles at the repo root). Moved here 2026-07-26, same reason as everything
else in this archive: story numbering is reused across phases, and these referenced
`_bmad-output/planning-artifacts/epics.md` directly, which would otherwise collide with
the next phase's Epic 2/3 work.
