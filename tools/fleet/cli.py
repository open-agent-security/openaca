from __future__ import annotations

import os
from pathlib import Path

import click
import httpx

from tools.fleet.client import FleetClient, FleetClientError
from tools.fleet.collector import (
    CollectError,
    clear_pending_uploads,
    collect_endpoint,
)
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
        preserved_asset_id = (
            existing.asset_id if existing.api_url == api_url and existing.token == token else None
        )
        if preserved_asset_id is None and existing.asset_id is not None:
            clear_pending_uploads()
        save_fleet_config(
            FleetConfig(api_url=api_url, token=token, asset_id=preserved_asset_id),
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
    except httpx.TransportError as exc:
        raise click.ClickException(f"Fleet API unreachable: {exc}") from exc
    except FleetClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Asset: {asset.display_name} ({asset.id})")
    click.echo(f"Latest BOM: {asset.latest_bom_id or 'none'}")
    click.echo(f"Last seen: {asset.last_seen_at or 'never'}")
    click.echo(f"Components: {asset.component_count} components")


@main.group()
def collect() -> None:
    """Collect and upload Fleet data."""


@collect.command()
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Agent host config directory. Defaults to $CLAUDE_CONFIG_DIR, else ~/.claude.",
)
@click.option(
    "--project",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root whose .claude settings/skills/MCPs are layered into endpoint resolution.",
)
@click.option("--quiet", is_flag=True, default=False, help="Minimize scheduled-run output.")
@click.option(
    "--allow-offline-cache",
    is_flag=True,
    default=False,
    help="Exit zero when upload fails after writing a pending cache file.",
)
def endpoint(
    config_dir: Path | None,
    project: Path | None,
    quiet: bool,
    allow_offline_cache: bool,
) -> None:
    """Collect endpoint composition and upload it."""
    try:
        result = collect_endpoint(
            config_dir=_resolve_endpoint_config_dir(config_dir),
            project=project,
            quiet=quiet,
            allow_offline_cache=allow_offline_cache,
        )
    except CollectError as exc:
        if not quiet:
            click.echo(str(exc), err=True)
        raise click.exceptions.Exit(exc.exit_code) from exc
    _print_upload_result(result)


def _print_upload_result(result) -> None:
    click.echo(f"Uploaded BOM: {result.bom_id}")
    click.echo(f"Asset: {result.asset_id}")
    click.echo(f"Components: {result.component_count}")
    click.echo(f"Findings: {result.finding_count}")
    click.echo(f"Policy violations: {result.policy_violation_count}")
    click.echo(f"Dashboard: {result.dashboard_url}")


def _resolve_endpoint_config_dir(config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def _mask_token(token: str) -> str:
    if token.startswith("ot_"):
        return "ot_..."
    return "***"
