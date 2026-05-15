#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud is required to download OSV data dumps" >&2
  exit 1
}
command -v uv >/dev/null 2>&1 || {
  echo "uv is required to run openaca seed" >&2
  exit 1
}

CACHE_DIR="${OPENACA_OSV_CACHE_DIR:-${TMPDIR:-/tmp}/openaca-osv}"
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
  if [[ -n "${OPENACA_LLM_PROVIDER:-}" || -n "${OPENACA_LLM_MODEL:-}" ]]; then
    seed_args+=(--llm-provider "${OPENACA_LLM_PROVIDER:-}")
    seed_args+=(--llm-model "${OPENACA_LLM_MODEL:-}")
  fi
  if [[ "${OPENACA_SEED_DRY_RUN:-}" == "1" ]]; then
    seed_args+=(--dry-run)
  fi
  if [[ -n "${OPENACA_SEED_LIMIT:-}" ]]; then
    seed_args+=(--limit "$OPENACA_SEED_LIMIT")
  fi
  uv run openaca seed "${seed_args[@]}"
}

seed_ecosystem "npm" ".openaca-seed-state-npm.json"
seed_ecosystem "PyPI" ".openaca-seed-state-pypi.json"
