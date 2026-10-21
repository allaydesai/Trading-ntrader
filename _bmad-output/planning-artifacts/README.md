# Planning Artifacts

Active BMAD planning cycle. The `bmad-create-prd` and `bmad-create-epics-and-stories`
workflows write `prd.md`, `epics.md`, and `architecture.md` here — they overwrite in
place, so each completed cycle is archived under `_bmad-output/archive/<phase>/` before
starting the next.

Currently empty: Phase 1 (Stocks) is archived at `../archive/phase-1-stocks/`, Phase 2
(ETFs) at `../archive/phase-2-etfs/`, and Phase 3 (Paper Trading) at
`../archive/phase-3-paper-trading/`. Ready for the next effort (Research MCP phase 5b, the
multi-strategy engine backtest). Research inputs for that PRD (`*research*.md`) may be
placed here ahead of the `bmad-create-prd` run.
