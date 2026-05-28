#!/usr/bin/env bash
# One-shot OpenACA scan with no persistent install.
#
# Public entry point:
#   curl -fsSL https://openaca.dev/scan | sh
#
# What this script does:
#   1. If `uv` is not on PATH, bootstrap it via Astral's installer.
#      uv is a small (~20MB) Python package manager; the only thing
#      this script installs persistently.
#   2. Use `uvx` to run openaca in an ephemeral environment, then
#      throw it away. OpenACA is NOT permanently installed.
#
# The default scan target is the user's endpoint (Claude Code +
# plugin + MCP configs in standard locations). Pass arbitrary args
# after the script name to override:
#   curl -fsSL https://openaca.dev/scan | sh -s -- scan repo --target .
#
# Environment variables:
#   OPENACA_VERSION   Pin the openaca release used for this scan.
#                     Default: latest.
#   UV_INSTALL_URL    Override the uv installer URL.
#   UV_INSTALL_DIR    Override where the uv binary is placed (default:
#                     ~/.local/bin). Respected by both uv's installer and
#                     this script's PATH update so they stay in sync.
#
# This is the "try-before-install" path. If you want openaca on your
# machine permanently, use install.sh instead.

set -eu

OPENACA_VERSION="${OPENACA_VERSION:-latest}"
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"

if ! command -v uvx >/dev/null 2>&1; then
  echo "→ Installing uv (small Python package manager; needed to run openaca without a permanent install)..."
  curl -LsSf "$UV_INSTALL_URL" | sh
  export PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
fi

# Build the `openaca` package spec uvx will resolve.
if [ "$OPENACA_VERSION" = "latest" ]; then
  OPENACA_SPEC="openaca"
else
  OPENACA_SPEC="openaca==${OPENACA_VERSION}"
fi

# Default scan target: endpoint. Caller can override by passing
# arguments via `sh -s --`. e.g.:
#   curl -fsSL https://openaca.dev/scan | sh -s -- scan repo --target .
if [ "$#" -eq 0 ]; then
  set -- scan endpoint
fi

echo "→ Running: uvx --isolated ${OPENACA_SPEC} $*"
echo ""
exec uvx --isolated "$OPENACA_SPEC" "$@"
