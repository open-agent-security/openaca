#!/usr/bin/env bash
set -euo pipefail

OPENACA_BIN="${OPENACA_BIN:-}"
MANAGED_SETTINGS_DIR="${OPENACA_POLICY_MANAGED_SETTINGS_DIR:-/Library/Application Support/ClaudeCode}"
CONSOLE_USER="${OPENACA_CONSOLE_USER:-$(stat -f %Su /dev/console)}"

if [ -z "$CONSOLE_USER" ] || [ "$CONSOLE_USER" = "root" ]; then
  echo "No logged-in console user found" >&2
  exit 3
fi

USER_HOME="$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory | awk '{print $2}')"
USER_PATH="$USER_HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if [ -z "$OPENACA_BIN" ]; then
  OPENACA_BIN="$USER_HOME/.local/bin/openaca"
fi
if [ ! -x "$OPENACA_BIN" ]; then
  echo "openaca is not installed for $CONSOLE_USER" >&2
  exit 4
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openaca-policy.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
chown "$CONSOLE_USER":staff "$WORK_DIR"
ARTIFACT_FILE="$WORK_DIR/50-openaca-policy.json"

run_as_user() {
  sudo -u "$CONSOLE_USER" env HOME="$USER_HOME" PATH="$USER_PATH" "$@"
}

# Retrieval, scanning, advisory evaluation, and compilation finish before the
# only managed-settings write below. Any failure leaves the installed artifact
# untouched.
run_as_user "$OPENACA_BIN" remote policy compile \
  --target "$USER_HOME/.claude" \
  --host claude \
  --managed-settings-dir "$MANAGED_SETTINGS_DIR" \
  --output "$ARTIFACT_FILE"

DROPIN_DIR="$MANAGED_SETTINGS_DIR/managed-settings.d"
DESTINATION="$DROPIN_DIR/50-openaca-policy.json"
install -d -m 0755 "$DROPIN_DIR"
STAGED_ARTIFACT="$(mktemp "$DROPIN_DIR/.50-openaca-policy.XXXXXX")"
trap 'rm -f "$STAGED_ARTIFACT"; rm -rf "$WORK_DIR"' EXIT
install -m 0644 "$ARTIFACT_FILE" "$STAGED_ARTIFACT"
mv -f "$STAGED_ARTIFACT" "$DESTINATION"
