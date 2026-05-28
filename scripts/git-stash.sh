#!/usr/bin/env bash
# Thin gate around git stash for the autofix allowlist.
# Disables core.hooksPath so a PR-controlled post-checkout hook (fired by
# stash's internal checkout) in scripts/git-hooks/ cannot execute arbitrary
# shell on a write-token runner.
set -euo pipefail

exec git -c core.hooksPath=/dev/null stash "$@"
