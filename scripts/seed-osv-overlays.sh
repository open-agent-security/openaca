#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud is required to download OSV data dumps" >&2
  exit 1
}
command -v uv >/dev/null 2>&1 || {
  echo "uv is required to run asve-seed" >&2
  exit 1
}

CACHE_DIR="${ASVE_OSV_CACHE_DIR:-${TMPDIR:-/tmp}/asve-osv}"
seed_ecosystem() {
  local ecosystem="$1"
  local state="$2"
  local dir="$CACHE_DIR/$ecosystem"

  mkdir -p "$dir"
  gcloud storage cp "gs://osv-vulnerabilities/$ecosystem/modified_id.csv" "$dir/modified_id.csv"
  gcloud storage cp "gs://osv-vulnerabilities/$ecosystem/all.zip" "$dir/all.zip"

  local seed_args=(
    --modified-index "$dir/modified_id.csv"
    --records-root "$dir"
    --state "$state"
  )
  if [[ -n "${ASVE_SEED_LLM_COMMAND:-}" ]]; then
    seed_args+=(--llm-command "$ASVE_SEED_LLM_COMMAND")
  fi
  if [[ "${ASVE_SEED_DRY_RUN:-}" == "1" ]]; then
    seed_args+=(--dry-run)
  fi
  uv run asve-seed "${seed_args[@]}"
}

seed_ecosystem "npm" ".asve-seed-state-npm.json"
seed_ecosystem "PyPI" ".asve-seed-state-pypi.json"
