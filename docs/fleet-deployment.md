# Fleet Deployment

OpenACA Fleet collection is opt-in. The scripts in `deploy/fleet/` configure the
logged-in macOS user and install a LaunchAgent that runs:

```sh
openaca fleet collect endpoint --quiet
```

The LaunchAgent runs every 6 hours and writes logs under
`~/Library/Logs/OpenACA/`.

## Required Variables

- `OPENACA_FLEET_TOKEN`: Fleet API token for the organization.
- `OPENACA_FLEET_API_URL`: optional API URL. Defaults to `https://api.openaca.dev`.
- `OPENACA_VERSION`: pinned `openaca` package version. Defaults to `0.1.0b6`.

The scripts install or update `uv`, install the pinned `openaca` CLI into the
console user's tool directory, configure Fleet, and load
`~/Library/LaunchAgents/com.openaca.fleet.plist`.

## Jamf

Use `deploy/fleet/jamf.sh`. You can provide variables as environment variables,
or use Jamf parameters:

- Parameter 4: `OPENACA_FLEET_TOKEN`
- Parameter 5: `OPENACA_FLEET_API_URL`
- Parameter 6: `OPENACA_VERSION`

## Kandji

Use `deploy/fleet/kandji.sh` as a custom script. Provide the required token via
Kandji's script environment variable support.

## Intune

Use `deploy/fleet/intune-macos.sh` as a macOS shell script. Provide the required
token through the script environment and run the script as root.

## Local Verification

After deployment, verify the user context:

```sh
launchctl print gui/$(id -u)/com.openaca.fleet
openaca fleet status
```
