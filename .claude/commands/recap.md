---
allowed-tools: Bash(git status:*), Bash(git diff --stat:*)
argument-hint: [optional focus, e.g. "just the bug fixes"]
description: Concise summary of what this session has achieved and known next steps
---

# Session Recap

A mid-session checkpoint. Summarize **only the current live conversation** — what we
set out to do, what got done, and what's left. Do not pull in BMAD sprint state, the
memory files, or unrelated git history; this is a snapshot of *this session's* work.

## Supporting evidence (this session's file changes)

Uncommitted changes (treat as artifacts produced this session):
!`git status --porcelain`

Changed lines by file:
!`git diff --stat`

## Optional focus

If provided, narrow the recap to this: **$ARGUMENTS**
(If empty, recap the whole session.)

## Instructions

Produce a **concise** summary — aim for one screen, no preamble. Use this structure,
omitting any section that has nothing real to report (don't pad):

1. **🎯 Goal** — one line: what this session is trying to accomplish, in the user's terms.

2. **✅ Achieved** — 3–6 bullets of concrete, *done* outcomes from this conversation.
   Ground each in something real (a decision made, a file edited, a test that passed,
   a question answered). Prefer the changes shown above over vague recollection.
   Reference files as `path:line` where useful. Do not list things merely discussed
   but not done — those go under Next Steps.

3. **🔜 Next steps** — bullets of known remaining work, in priority order. Pull these
   from: explicit TODOs raised this session, things we said we'd "do next," obvious
   follow-ups to what we changed (tests to run, things to commit), and any open
   questions still blocking progress. Mark anything blocked with **(blocked: …)**.

4. **⚠️ Watch-outs** — *only if any exist* — gotchas, half-finished edits, uncommitted
   work at risk, or assumptions we should confirm before continuing.

Rules:
- Be honest: if a task failed, was skipped, or is unverified, say so plainly.
- No filler, no restating these instructions, no "in this session we…" throat-clearing.
- If the session has barely started and there's little to report, say that in one line
  rather than inventing achievements.
