#!/usr/bin/env bash
set -euo pipefail

if [ -z "${OPENACA_REMOTE_TOKEN:-}" ] && [ -n "${4:-}" ]; then
  OPENACA_REMOTE_TOKEN="$4"
fi
if [ -z "${OPENACA_REMOTE_API_URL:-}" ] && [ -n "${5:-}" ]; then
  OPENACA_REMOTE_API_URL="$5"
fi
if [ -z "${OPENACA_VERSION:-}" ] && [ -n "${6:-}" ]; then
  OPENACA_VERSION="$6"
fi

OPENACA_REMOTE_API_URL="${OPENACA_REMOTE_API_URL:-https://api.openaca.dev}"
OPENACA_VERSION="${OPENACA_VERSION:-latest}"
LABEL="com.openaca.remote"

if [ -z "${OPENACA_REMOTE_TOKEN:-}" ]; then
  echo "OPENACA_REMOTE_TOKEN is required, or pass it as Jamf parameter 4" >&2
  exit 2
fi

CONSOLE_USER="${OPENACA_CONSOLE_USER:-$(stat -f %Su /dev/console)}"
if [ -z "$CONSOLE_USER" ] || [ "$CONSOLE_USER" = "root" ]; then
  echo "No logged-in console user found" >&2
  exit 3
fi

USER_HOME="$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory | awk '{print $2}')"
USER_UID="$(id -u "$CONSOLE_USER")"
USER_PATH="$USER_HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
UV_BIN="$USER_HOME/.local/bin/uv"
OPENACA_BIN="$USER_HOME/.local/bin/openaca"
PLIST="$USER_HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$USER_HOME/Library/Logs/OpenACA"

run_as_user() {
  sudo -u "$CONSOLE_USER" env HOME="$USER_HOME" PATH="$USER_PATH" "$@"
}

if [ ! -x "$UV_BIN" ]; then
  run_as_user sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
else
  run_as_user "$UV_BIN" self update >/dev/null 2>&1 || true
fi

if [ "$OPENACA_VERSION" = "latest" ]; then
  run_as_user "$UV_BIN" tool install --upgrade --prerelease allow openaca
else
  run_as_user "$UV_BIN" tool install --upgrade "openaca==$OPENACA_VERSION"
fi
run_as_user "$OPENACA_BIN" remote configure \
  --api-url "$OPENACA_REMOTE_API_URL" \
  --token "$OPENACA_REMOTE_TOKEN"

run_as_user mkdir -p "$LOG_DIR" "$USER_HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$OPENACA_BIN</string>
    <string>remote</string>
    <string>sync</string>
    <string>endpoint</string>
    <string>--quiet</string>
  </array>
  <key>StartInterval</key>
  <integer>21600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/remote.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/remote.err.log</string>
</dict>
</plist>
PLIST

chown "$CONSOLE_USER":staff "$PLIST"
chmod 0644 "$PLIST"
launchctl bootout "gui/$USER_UID" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$USER_UID" "$PLIST"
launchctl kickstart -k "gui/$USER_UID/$LABEL" >/dev/null 2>&1 || true

echo "OpenACA remote LaunchAgent installed for $CONSOLE_USER"
