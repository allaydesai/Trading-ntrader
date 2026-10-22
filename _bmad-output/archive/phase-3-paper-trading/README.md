# Archive — Phase 3 (Paper Trading)

Snapshot of the completed Phase 3 BMAD cycle — paper trading sessions against the IBKR
paper Gateway through a Nautilus `TradingNode`. Closed 2026-10-13 by Allay: all 5 epics
done, PRD phase-complete criteria met on live evidence (see `sprint-status.yaml`,
`last_updated` 2026-10-13). Merged to `015-paper-trading` through PRs #35 (Epic 4) and
#36 (Epic 5).

## Planning artifacts

These are the planning artifacts as they stood at completion. They were moved out of
`_bmad-output/planning-artifacts/` on 2026-10-21 so the next effort (Research MCP phase 5b,
the multi-strategy engine backtest) can regenerate `prd.md` / `epics.md` /
`architecture.md` on a clean slate without overwriting Phase 3.

- `prd.md` — Phase 3 PRD
- `prd-epic1-scope.md`, `prd-epic4-scope.md`, `prd-epic5-scope.md` — per-epic extracts, so
  the acceptance-criterion traceability gate only sees one epic's criteria at a time. Tests
  still cite these by their old `_bmad-output/planning-artifacts/...` path in
  `@pytest.mark.acceptance_criterion` markers; the path is reporting text only (no file is
  opened), as it was for Phases 1 and 2
- `epics.md` — Phase 3 epic/story breakdown (5 epics, 35 stories, plus 5.7 and 5.8 added
  2026-09-30 from PR #35's code-review rulings)
- `architecture.md` — Phase 3 solution architecture (D1–D8, AR addenda)
- `implementation-readiness-report-2026-08-03.md` — readiness gate

## Implementation history

`implementation-artifacts/` — the permanent Phase 3 build record: story files (`1-1`…`5-9`),
`epic-1`…`epic-4` retros (the Epic 5 retro was skipped by PO ruling), `epic4-prework-spec.md`,
`deferred-work.md` and `sprint-status.yaml`. Moved here for the same reason as Phases 1 and
2: epic/story numbering is reused across phases, and the live folder must be empty before
the next cycle's `1-x` stories are created.

`deferred-work.md` carries open, non-blocking items at close, collected under "Phase 3
close-out: remaining after sign-off (2026-10-13)" (P6 full-day soak, operator-only
procedures P17b/P18/P20/P22, P4 partial, the 5.9 DN1/P9 hook). Check that section before
assuming anything there is closed. One entry is already resolved after the close: the
"`trades` has no `strategy_id` column" item (and Story 5.6's disposition of it) was
closed by commit 937bee0 (migration `dc41856246ad`, 2026-10-21) as phase 5b step 1 of the
Research MCP; the entries themselves were not edited.
