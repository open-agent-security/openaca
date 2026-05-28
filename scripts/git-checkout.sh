#!/usr/bin/env bash
# Thin gate around git checkout for the autofix allowlist.
# Disables core.hooksPath so a PR-controlled post-checkout hook in
# scripts/git-hooks/ cannot execute arbitrary shell on a write-token runner.
set -euo pipefail

exec git -c core.hooksPath=/dev/null checkout "$@"
