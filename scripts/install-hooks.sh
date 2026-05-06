#!/usr/bin/env bash
# Configure git to use scripts/git-hooks/ as the hooks directory via the
# SHARED repo config, so every worktree (current + future) inherits it.
# Run once per checkout; new worktrees pick it up automatically.
#
# Usage: bash scripts/install-hooks.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

HOOKS_DIR="scripts/git-hooks"
HOOK_FILE="$HOOKS_DIR/pre-push"

if [ ! -f "$HOOK_FILE" ]; then
  echo "✗ expected $HOOK_FILE not found — is this a fresh checkout?"
  exit 1
fi

if [ ! -x "$HOOK_FILE" ]; then
  chmod +x "$HOOK_FILE"
fi

current="$(git config --get core.hooksPath || true)"
if [ "$current" = "$HOOKS_DIR" ]; then
  echo "✓ core.hooksPath already set to $HOOKS_DIR (shared — applies to all worktrees)"
  exit 0
fi

git config core.hooksPath "$HOOKS_DIR"
if [ -n "$current" ]; then
  echo "⚠ replaced shared core.hooksPath ($current → $HOOKS_DIR)"
else
  echo "✓ shared core.hooksPath set to $HOOKS_DIR (applies to all worktrees)"
fi
