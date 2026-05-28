#!/usr/bin/env bash
# Thin gate around git rebase for the autofix allowlist.
# Rejects --exec/-x/--interactive/-i — those flags let arbitrary shell
# commands run at each rebase step on a runner with write tokens, bypassing
# the Bash allowlist and the push-as-only-egress security boundary.
set -euo pipefail

for arg in "$@"; do
  case "$arg" in
    --exec | --exec=* | -x* | -i* | --interactive | --interactive=*)
      printf '::error::git rebase %s is blocked (exec/interactive rebase disabled in autofix context)\n' "$arg" >&2
      exit 1
      ;;
  esac
done

# -c core.hooksPath=/dev/null prevents a PR-controlled pre-rebase hook
# (planted in scripts/git-hooks/ before install-hooks.sh runs) from
# executing arbitrary shell on a write-token runner.
exec git -c core.hooksPath=/dev/null rebase "$@"
