# Fleet Deploy Script Tests

## Goal

Add automated coverage for the macOS Fleet deployment scripts without requiring
Jamf, Kandji, Intune, or a managed Mac in CI.

## Plan

- [x] Add syntax coverage for all scripts with `bash -n`.
- [x] Add a stubbed macOS command harness for `stat`, `dscl`, `id`, `sudo`,
  `uv`, `openaca`, `launchctl`, and `chown`.
- [x] Verify required-token and no-console-user failure modes.
- [x] Verify Jamf parameter 4/5/6 mapping.
- [x] Verify each script installs the selected `openaca` package, configures
  Fleet with the provided token/API URL, writes the LaunchAgent plist, and
  loads/kickstarts it.
- [x] Run the targeted deploy-script tests.
- [x] Run the relevant formatter/linter/test checks.
