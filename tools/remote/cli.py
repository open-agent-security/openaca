from __future__ import annotations

import json
from pathlib import Path

import click
import httpx

from tools.cli_kind import kind_option, require_kind_for_config_dir
from tools.policy import PolicyEvaluationError, PolicyValidationError, parse
from tools.policy_cli import compile_endpoint_policy, emit_policy_report
from tools.remote.client import RemoteClient, RemoteClientError
from tools.remote.collector import (
    CollectError,
    build_endpoint_dry_run_payloads,
    clear_pending_uploads,
    collect_endpoint,
)
from tools.remote.config import (
    DEFAULT_API_URL,
    ConfigError,
    RemoteConfig,
    get_config_path,
    load_remote_config,
    save_remote_config,
)
from tools.remote.upload_contract import RemoteUploadContractError


@click.group()
def main() -> None:
    """Configure remote endpoint services."""


@main.command()
@click.option(
    "--token",
    envvar="OPENACA_REMOTE_TOKEN",
    prompt="Remote API token",
    hide_input=True,
    help="Remote API token.",
)
@click.option("--api-url", default=DEFAULT_API_URL, show_default=True, help="Remote API URL.")
def configure(token: str, api_url: str) -> None:
    """Write local remote configuration."""
    config_path = get_config_path()
    try:
        existing = load_remote_config(config_path)
        preserved_asset_id = (
            existing.asset_id if existing.api_url == api_url and existing.token == token else None
        )
        if preserved_asset_id is None and existing.asset_id is not None:
            clear_pending_uploads()
        save_remote_config(
            RemoteConfig(api_url=api_url, token=token, asset_id=preserved_asset_id),
            config_path,
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Remote configured at {api_url} with token {_mask_token(token)}")


@main.command()
def status() -> None:
    """Show remote token and asset status."""
    try:
        config = load_remote_config(get_config_path())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if config.token is None:
        raise click.ClickException(
            "Remote is not configured; run openaca remote configure --token <TOKEN>"
        )

    client = RemoteClient(api_url=config.api_url, token=config.token)
    try:
        me = client.get_me()
        click.echo(f"Org: {me.org.name} ({me.org.id})")
        click.echo(f"Token: {me.token.name} ({me.token.id})")
        if config.asset_id is None:
            click.echo("No asset configured yet. Run openaca remote sync endpoint first.")
            return
        asset = client.get_asset(config.asset_id)
    except httpx.TransportError as exc:
        raise click.ClickException(f"Remote API unreachable: {exc}") from exc
    except RemoteClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Asset: {asset.display_name} ({asset.id})")
    # Per agent, because an asset has no BOM of its own: one upload per agent
    # kind, each with its own id. Reading the asset-level id Fleet removed is
    # why this line said "Latest BOM: none" right after a successful upload.
    if asset.agents:
        click.echo("Latest BOM:")
        for agent in asset.agents:
            coverage = f" ({agent.composition_coverage})" if agent.composition_coverage else ""
            click.echo(f"  {agent.agent_kind}: {agent.latest_bom_id or 'none'}{coverage}")
    else:
        click.echo("Latest BOM: none — no agent has synced yet")
    click.echo(f"Last seen: {asset.last_seen_at or 'never'}")
    click.echo(f"Components: {asset.component_count} components")


@main.group()
def sync() -> None:
    """Collect and upload remote data."""


@main.group()
def policy() -> None:
    """Fetch and compile the organization policy for one endpoint."""


@policy.command()
@click.option("--target", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--project", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--host", type=click.Choice(["claude"]), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--managed-settings-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text")
def compile(
    target: Path,
    project: Path | None,
    host: str,
    output: Path | None,
    managed_settings_dir: Path | None,
    dry_run: bool,
    output_format: str,
) -> None:
    """Fetch the organization policy and compile it for one endpoint."""
    if output is None and not dry_run:
        raise click.UsageError("--output is required unless --dry-run is set")
    try:
        config = load_remote_config(get_config_path())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if config.token is None:
        raise click.ClickException(
            "Remote is not configured; run openaca remote configure --token <TOKEN>"
        )

    client = RemoteClient(api_url=config.api_url, token=config.token)
    try:
        document = client.get_policy_document()
        if document is None:
            raise click.ClickException("Remote has no policy; existing artifact was not changed")
        policy_document = parse(document)
        compilation = compile_endpoint_policy(
            policy_document,
            target=target,
            project=project,
            output=output,
            managed_settings_dir=managed_settings_dir,
            dry_run=dry_run,
        )
    except httpx.TransportError as exc:
        raise click.ClickException(f"Remote API unreachable: {exc}") from exc
    except (RemoteClientError, PolicyValidationError, PolicyEvaluationError) as exc:
        raise click.ClickException(str(exc)) from exc

    emit_policy_report(compilation, output_format)
    if project is None:
        click.echo(
            "note: project-local configuration was not scanned; pass --project to include it",
            err=True,
        )


@sync.command()
@kind_option
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Agent host config directory for the kind selected with --kind. "
        "Requires --kind. Each kind resolves its own default root when "
        "omitted (Claude Code: $CLAUDE_CONFIG_DIR, else ~/.claude; Cursor: "
        "~/.cursor)."
    ),
)
@click.option(
    "--project",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root whose .claude settings/skills/MCPs are layered into endpoint resolution.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the payload that would be uploaded, as NDJSON, and exit without uploading.",
)
@click.option("--quiet", is_flag=True, default=False, help="Minimize scheduled-run output.")
@click.option(
    "--allow-offline-cache",
    is_flag=True,
    default=False,
    help="Exit zero when upload fails after writing a pending cache file.",
)
@click.option(
    "--scanner",
    "external_scanners",
    type=click.Choice(["nvidia-skillspector"]),
    multiple=True,
    help="Run an optional external scanner before upload. May be repeated.",
)
def endpoint(
    kind: str | None,
    config_dir: Path | None,
    project: Path | None,
    dry_run: bool,
    quiet: bool,
    allow_offline_cache: bool,
    external_scanners: tuple[str, ...],
) -> None:
    """Sync endpoint composition to the configured remote."""
    require_kind_for_config_dir(kind, config_dir)
    if dry_run:
        _dry_run_endpoint(
            config_dir=config_dir,
            kind_id=kind,
            project=project,
            external_scanners=external_scanners,
        )
        return
    try:
        results = collect_endpoint(
            config_dir=config_dir,
            kind_id=kind,
            project=project,
            quiet=quiet,
            allow_offline_cache=allow_offline_cache,
            external_scanners=external_scanners,
        )
    except CollectError as exc:
        if not quiet:
            click.echo(str(exc), err=True)
        raise click.exceptions.Exit(exc.exit_code) from exc
    except RemoteUploadContractError as exc:
        if not quiet:
            click.echo(str(exc), err=True)
        raise click.exceptions.Exit(1) from exc
    _print_upload_results(results)


def _dry_run_endpoint(
    *,
    config_dir: Path | None,
    kind_id: str | None,
    project: Path | None,
    external_scanners: tuple[str, ...],
) -> None:
    """Print what a sync would upload, one payload per line.

    NDJSON rather than a wrapper object: one payload is a single line and
    therefore still whole JSON for `jq` and `json.load`, and the shape does
    not change when a scan resolves several agents. Nothing else goes to
    stdout, so the preview stays pipeable.
    """
    try:
        payloads = build_endpoint_dry_run_payloads(
            config_dir=config_dir,
            kind_id=kind_id,
            project=project,
            external_scanners=external_scanners,
        )
    except CollectError as exc:
        click.echo(str(exc), err=True)
        raise click.exceptions.Exit(exc.exit_code) from exc
    except (ConfigError, RemoteUploadContractError) as exc:
        raise click.ClickException(str(exc)) from exc
    for payload in payloads:
        click.echo(json.dumps(payload, sort_keys=True))


def _print_upload_results(results) -> None:
    for index, result in enumerate(results):
        if index:
            click.echo("")
        _print_upload_result(result)


def _print_upload_result(result) -> None:
    click.echo(f"Uploaded BOM: {result.bom_id}")
    click.echo(f"Asset: {result.asset_id}")
    click.echo(f"Components: {result.component_count}")
    click.echo(f"Findings: {result.finding_count}")
    if result.policy_violation_count is not None:
        click.echo(f"Policy violations: {result.policy_violation_count}")
    click.echo(f"Dashboard: {result.dashboard_url}")


def _mask_token(token: str) -> str:
    # Prefix + last 4 — the same last-4 the backend stores for console
    # display — so a user with several tokens can tell which one is
    # configured. Suffix only when the token is long enough that 4 chars
    # reveal little; real tokens are ot_ + 20+ chars.
    if token.startswith("ot_"):
        if len(token) >= 23:  # ot_ (3) + 20+ real secret chars
            return f"ot_...{token[-4:]}"
        return "ot_..."
    return "***"
