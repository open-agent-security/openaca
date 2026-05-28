from __future__ import annotations

import click

from tools.fleet.client import FleetClient, FleetClientError
from tools.fleet.config import (
    DEFAULT_API_URL,
    ConfigError,
    FleetConfig,
    get_config_path,
    load_fleet_config,
    save_fleet_config,
)


@click.group()
def main() -> None:
    """Configure opt-in Fleet uploads."""


@main.command()
@click.option("--token", prompt="Fleet API token", hide_input=True, help="Fleet API token.")
@click.option("--api-url", default=DEFAULT_API_URL, show_default=True, help="Fleet API URL.")
def configure(token: str, api_url: str) -> None:
    """Write local Fleet configuration."""
    config_path = get_config_path()
    try:
        existing = load_fleet_config(config_path)
        save_fleet_config(
            FleetConfig(api_url=api_url, token=token, asset_id=existing.asset_id),
            config_path,
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Fleet configured at {api_url} with token {_mask_token(token)}")


@main.command()
def status() -> None:
    """Show Fleet token and asset status."""
    try:
        config = load_fleet_config(get_config_path())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if config.token is None:
        raise click.ClickException(
            "Fleet is not configured; run openaca fleet configure --token <TOKEN>"
        )

    client = FleetClient(api_url=config.api_url, token=config.token)
    try:
        me = client.get_me()
        click.echo(f"Org: {me.org.name} ({me.org.id})")
        click.echo(f"Token: {me.token.name} ({me.token.id})")
        if config.asset_id is None:
            click.echo("No asset configured yet. Run openaca fleet collect endpoint first.")
            return
        asset = client.get_asset(config.asset_id)
    except FleetClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Asset: {asset.display_name} ({asset.id})")
    click.echo(f"Latest BOM: {asset.latest_bom_id or 'none'}")
    click.echo(f"Last seen: {asset.last_seen_at or 'never'}")
    click.echo(f"Components: {asset.component_count} components")


def _mask_token(token: str) -> str:
    if token.startswith("ot_"):
        return "ot_..."
    return "***"
