#!/usr/bin/env bash
# Prepare a fresh workspace (git worktree). Idempotent; safe to re-run.
#   Superset: wired via .superset/config.json "setup"
#   T3 Code:  set the project's setup script to ./scripts/workspace/setup.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$WORKTREE"
log "worktree: $WORKTREE"
log "root:     $ROOT"

# 1. Untracked files from the main checkout. Never overwrite: a workspace's own edits win.
if ! is_main_checkout; then
  for rel in .env .env.dev .env.qa .claude/settings.local.json; do
    if [[ -f "$ROOT/$rel" && ! -e "$rel" ]]; then
      mkdir -p "$(dirname "$rel")"
      cp -p "$ROOT/$rel" "$rel"
      log "copied $rel"
    fi
  done

  # Parquet catalogs are huge (100s of GB): share them by symlink, never copy.
  # (data/catalog/ is partly tracked, so it is left as the worktree's own copy.)
  if [[ -d "$ROOT/data/catalogs" && ! -e data/catalogs ]]; then
    ln -s "$ROOT/data/catalogs" data/catalogs
    log "linked data/catalogs -> $ROOT/data/catalogs"
  fi

  # Tailwind standalone CLI (~48 MB): link instead of re-downloading.
  if [[ -x "$ROOT/tailwindcss" && ! -e tailwindcss ]]; then
    ln -s "$ROOT/tailwindcss" tailwindcss
    log "linked tailwindcss"
  fi
fi
[[ -f .env ]] || { cp .env.example .env; log "no .env in root; created one from .env.example (edit it)"; }

# 2. Strategies submodule (needs GitHub SSH access; non-fatal so setup still finishes).
git submodule update --init --recursive \
  || log "WARNING: submodule init failed (src/core/strategies/custom); run 'git submodule update --init' by hand"

# 3. Python deps (uv's global cache makes this fast after the first workspace).
uv sync --frozen

# 4. Git hooks (shared git config; harmless if already set).
git config core.hooksPath .githooks

# 5. Dev-server port for this workspace.
mkdir -p "$STATE_DIR"
claim_port >"$PORT_FILE"
log "web port: $(cat "$PORT_FILE")  (run: ./scripts/workspace/run.sh)"
log "done. DB is shared (Homebrew Postgres): migrations are NOT run here."
