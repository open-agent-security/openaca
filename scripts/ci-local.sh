#!/usr/bin/env bash
# ci-local.sh — reproduce `.github/workflows/ci.yml` before pushing.
#
# Both CI jobs, in order, against a *clean checkout of HEAD* rather than the
# working tree. That last part is the point: a test fixture that is untracked or
# matched by a gitignore rule (including your global ~/.config/git/ignore) still
# exists on your disk, so `uv run pytest` passes locally and fails in CI, which
# only ever sees committed files. Running the suite from `git archive HEAD` is
# what catches that.
#
# Usage:
#   scripts/ci-local.sh            # lint-and-test + smoke-install
#   scripts/ci-local.sh fast       # lint-and-test only (skips the wheel build)
#   scripts/ci-local.sh dirty      # lint-and-test against the working tree
#
# Exits non-zero on the first failing gate, printing which one.

set -uo pipefail

MODE="${1:-all}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

WORK=""
cleanup() { [[ -n "$WORK" && -d "$WORK" ]] && rm -rf "$WORK"; }
trap cleanup EXIT

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# ── Uncommitted-work warning ────────────────────────────────────────────────
# CI tests the pushed commit, so anything unstaged is invisible to it. Say so
# rather than silently testing something other than what you are about to push.
if [[ "$MODE" != "dirty" ]] && ! git diff-index --quiet HEAD --; then
  printf '\033[33m! uncommitted changes present — testing HEAD, not your working tree\033[0m\n'
  git status --short | sed 's/^/    /'
fi

# ── Files CI will never see ─────────────────────────────────────────────────
# An ignored or untracked file under tests/ is the highest-value warning this
# script emits: it is exactly the false-green that motivated writing it.
untracked_tests="$(git status --porcelain --ignored=matching -- tests/ |
  grep -E '^(\?\?|!!)' | awk '{print $2}' || true)"
if [[ -n "$untracked_tests" ]]; then
  printf '\033[33m! untracked/ignored files under tests/ — CI will not have these:\033[0m\n'
  printf '%s\n' "$untracked_tests" | sed 's/^/    /'
fi

# ── Job 1: lint-and-test ────────────────────────────────────────────────────
if [[ "$MODE" == "dirty" ]]; then
  TEST_DIR="$REPO_ROOT"
  step "lint-and-test (working tree)"
else
  WORK="$(mktemp -d)"
  TEST_DIR="$WORK/repo"
  mkdir -p "$TEST_DIR"
  git archive HEAD | tar -x -C "$TEST_DIR"
  step "lint-and-test (clean checkout of HEAD)"
fi

cd "$TEST_DIR"
uv sync --frozen                        || fail "uv sync --frozen (uv.lock stale vs pyproject.toml?)"
uv run ruff check .                     || fail "ruff check"
uv run ruff format --check .            || fail "ruff format --check"
uv run pyright                          || fail "pyright"
uv run pytest --cov=tools --cov-report=term-missing \
                                        || fail "pytest"
if [[ -d overlays ]] && find overlays -name '*.yaml' -print -quit | grep -q .; then
  uv run openaca lint overlays/         || fail "openaca lint overlays/"
fi
if [[ -d capabilities ]] && find capabilities -name '*.yaml' -print -quit | grep -q .; then
  uv run openaca lint capabilities/     || fail "openaca lint capabilities/"
fi
cd "$REPO_ROOT"

[[ "$MODE" == "fast" ]] && { printf '\n\033[32m✓ lint-and-test passed (smoke-install skipped)\033[0m\n'; exit 0; }

# ── Job 2: smoke-install ────────────────────────────────────────────────────
# The wheel installed with deps resolved fresh, mirroring `uv tool install`
# rather than the locked dev env, then the user-facing CLI surfaces.
step "smoke-install (wheel, deps resolved fresh)"
SMOKE="$(mktemp -d)"
trap 'cleanup; rm -rf "$SMOKE"' EXIT

uv build --wheel --out-dir "$SMOKE/dist" >/dev/null || fail "uv build --wheel"
uv venv "$SMOKE/venv" >/dev/null
uv pip install --python "$SMOKE/venv/bin/python" --prerelease=explicit \
  "$SMOKE"/dist/*.whl >/dev/null                    || fail "uv pip install (dep constraint too loose?)"

openaca="$SMOKE/venv/bin/openaca"
"$openaca" --version                                || fail "openaca --version"

# `.mcp.json`, not `mcp.json`: a bare `mcp.json` is owned by no runtime
# exclusively, so it declares no agent and the repo emits no BOM (ADR-0044).
mkdir -p "$SMOKE/target"
cat > "$SMOKE/target/.mcp.json" <<'FIXTURE'
{"mcpServers": {"remote": {"type": "http", "url": "https://api.example.test/mcp"}}}
FIXTURE
"$openaca" scan repo --target "$SMOKE/target" --include-posture --fail-on none >/dev/null \
                                                    || fail "openaca scan repo"
"$openaca" bom repo --target "$SMOKE/target" --output "$SMOKE/bom.json" >/dev/null \
                                                    || fail "openaca bom repo"
[[ -s "$SMOKE/bom.json" ]]                          || fail "bom repo wrote no document"

HOME="$SMOKE/home"; mkdir -p "$HOME"; export HOME
OPENACA_REMOTE_TOKEN=ot_SMOKE "$openaca" remote configure --api-url http://127.0.0.1:9 >/dev/null \
                                                    || fail "openaca remote configure"
out="$("$openaca" remote sync endpoint 2>&1)"; code=$?
[[ "$code" -eq 2 ]] || fail "remote sync: expected exit 2 (clean network failure), got $code"
grep -q Traceback <<<"$out" && fail "remote sync crashed instead of failing cleanly"

printf '\n\033[32m✓ both CI jobs passed locally\033[0m\n'
