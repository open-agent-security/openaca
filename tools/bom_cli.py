"""`openaca bom` commands for emitting Agent BOMs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from tools.bom import build_agent_bom
from tools.bom_lint import main as lint_cmd
from tools.parsers import flatten_grouped, parse_repo_grouped
from tools.parsers.claude_install import parse_install
from tools.scan import _filter_agent_scope_refs


@click.group()
def main() -> None:
    """Generate OpenACA Agent BOMs."""


main.add_command(lint_cmd, name="lint")


@main.command()
@click.option(
    "--target",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to inspect.",
)
@click.option(
    "--include-gitignored",
    is_flag=True,
    default=False,
    help="Walk paths matched by <target>/.gitignore.",
)
def repo(target: Path, include_gitignored: bool) -> None:
    """Generate an Agent BOM from repository manifests."""
    grouped, n_found = parse_repo_grouped(target, include_gitignored=include_gitignored)
    n_parsed = len(grouped)
    if n_found > n_parsed:
        click.echo(
            f"warning: {n_found - n_parsed} of {n_found} matched manifest(s) failed to parse"
            " and were skipped",
            err=True,
        )
    refs = _filter_agent_scope_refs(flatten_grouped(grouped))
    bom = build_agent_bom(refs, target_type="repo", target=str(target))
    click.echo(json.dumps(bom.to_cyclonedx(), indent=2))


@main.command()
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
def endpoint(config_dir: Path | None, project: Path | None) -> None:
    """Generate an Agent BOM from active endpoint composition."""
    config_dir = _resolve_endpoint_config_dir(config_dir)
    refs, warnings = parse_install(
        install_root=config_dir,
        project_root=project,
        mode="endpoint",
        include_transitive=True,
    )
    for warning in warnings:
        click.echo(f"warning: {warning}", err=True)
    bom = build_agent_bom(refs, target_type="endpoint", target=str(config_dir))
    click.echo(json.dumps(bom.to_cyclonedx(), indent=2))


def _resolve_endpoint_config_dir(config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"
