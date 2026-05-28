#!/usr/bin/env bash
# Persistent install for OpenACA via uv.
#
# Public entry point:
#   curl -fsSL https://openaca.dev/install.sh | sh
#
# What this script does:
#   1. If `uv` is not on PATH, bootstrap it via Astral's installer.
#      uv is a self-contained Python package manager that handles
#      Python version, isolation, and PATH automatically — sidesteps
#      PEP 668, system-Python contamination, and "which Python is this"
#      issues that plain `pip install openaca` runs into.
#   2. Use `uv tool install` to install openaca into an isolated
#      environment under uv's tool directory.
#
# Environment variables:
#   OPENACA_VERSION   Pin the installed version. Default: latest.
#                     For Fleet / MDM / CI use, set this to a specific
#                     pre-release (e.g. "0.2.0b1") for reproducibility.
#   UV_INSTALL_URL    Override the uv installer URL. Default points to
#                     astral.sh/uv. Mainly useful for air-gapped or
#                     mirror setups.
#   UV_INSTALL_DIR    Override where the uv binary is placed (default:
#                     ~/.local/bin). Respected by both uv's installer and
#                     this script's PATH update so they stay in sync.
#   UV_TOOL_BIN_DIR   Override where uv writes tool executables.
#
# This script is intentionally short and inspectable. Read it before
# piping to sh if you're security-conscious; the README documents both
# the trade-off and the manual install path as alternatives.

set -eu

OPENACA_VERSION="${OPENACA_VERSION:-latest}"
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"

# Bootstrap uv if not already installed. uv's installer puts the binary
# at ~/.local/bin/uv on Unix systems.
if ! command -v uv >/dev/null 2>&1; then
  echo "→ Installing uv (Python package manager from Astral)..."
  curl -LsSf "$UV_INSTALL_URL" | sh
  # Make uv available in this shell session. The uv installer also
  # updates the user's shell config for future sessions.
  # Use UV_INSTALL_DIR if set so this PATH update matches wherever
  # the installer actually wrote the binary.
  export PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
fi

# Install openaca into uv's isolated tool environment.
echo "→ Installing openaca (${OPENACA_VERSION})..."
if [ "$OPENACA_VERSION" = "latest" ]; then
  uv tool install --upgrade openaca
else
  uv tool install --upgrade "openaca==${OPENACA_VERSION}"
fi
uv tool update-shell >/dev/null 2>&1 || true

TOOL_BIN_DIR="$(uv tool dir --bin)"
OPENACA_BIN="$TOOL_BIN_DIR/openaca"

echo ""
echo "✓ openaca installed."
echo ""
echo "Try a scan:"
if command -v openaca >/dev/null 2>&1; then
  echo "  openaca scan endpoint"
elif [ -x "$OPENACA_BIN" ]; then
  echo "  $OPENACA_BIN scan endpoint"
else
  echo "  uvx openaca scan endpoint"
fi
echo ""
echo "If your shell cannot find 'openaca', add uv's tool directory to PATH:"
echo "  export PATH=\"$TOOL_BIN_DIR:\$PATH\""
echo ""
echo "Pin a specific version (recommended for MDM/CI deploys):"
echo "  curl -fsSL https://openaca.dev/install.sh | OPENACA_VERSION=0.1.0b5 sh"
