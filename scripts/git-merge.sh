#!/usr/bin/env bash
# Thin gate around git merge for the autofix allowlist.
# Disables core.hooksPath so a PR-controlled pre-merge-commit or post-merge
# hook in scripts/git-hooks/ cannot execute arbitrary shell on a write-token
# runner.
set -euo pipefail

exec git -c core.hooksPath=/dev/null merge "$@"
