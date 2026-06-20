"""`openaca bom` commands for emitting Agent BOMs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from tools.bom import build_agent_bom
from tools.bom_lint import main as lint_cmd
from tools.graph_build import build_graph
from tools.parsers import parse_repo_grouped
from tools.scan import _filter_agent_scope_refs, _is_plugin_ref, _refs_from_graph


@click.group()
def main() -> None:
    """Generate OpenACA Agent BOMs."""


main.add_command(lint_cmd, name="lint")


_output_option = click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the CycloneDX Agent BOM JSON to this file instead of stdout.",
)


def _emit_bom_json(document: dict, output_path: Path | None) -> None:
    rendered = json.dumps(document, indent=2)
    if output_path is None:
        click.echo(rendered)
        return
    try:
        output_path.write_text(f"{rendered}\n", encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"failed to write BOM to {output_path}: {exc}") from exc


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
@_output_option
def repo(target: Path, include_gitignored: bool, output_path: Path | None) -> None:
    """Generate an Agent BOM from repository manifests."""
    graph = build_graph(target, mode="repo", include_gitignored=include_gitignored)
    # Manifest-visited count and parse-failure reporting are properties of the
    # filesystem walk, not the graph; source them from the walk so the warning
    # is unchanged. Composition (nodes/edges/scope) comes from the graph.
    parse_groups, n_found = parse_repo_grouped(target, include_gitignored=include_gitignored)
    n_parsed = len(parse_groups)
    if n_found > n_parsed:
        click.echo(
            f"warning: {n_found - n_parsed} of {n_found} matched manifest(s) failed to parse"
            " and were skipped",
            err=True,
        )
    bom = build_agent_bom(
        _filter_agent_scope_refs(_refs_from_graph(graph)),
        target_type="repo",
        target=str(target),
        source_unit_count=n_found,
        source_unit_label="manifest",
        graph=graph,
    )
    _emit_bom_json(bom.to_cyclonedx(), output_path)


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
@_output_option
def endpoint(config_dir: Path | None, project: Path | None, output_path: Path | None) -> None:
    """Generate an Agent BOM from active endpoint composition."""
    config_dir = _resolve_endpoint_config_dir(config_dir)
    graph = build_graph(config_dir, mode="endpoint", project_root=project)
    refs = _refs_from_graph(graph)
    bom = build_agent_bom(
        _filter_agent_scope_refs(refs),
        target_type="endpoint",
        target=str(config_dir),
        source_unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
        source_unit_label="active plugin",
        graph=graph,
    )
    _emit_bom_json(bom.to_cyclonedx(), output_path)


def _resolve_endpoint_config_dir(config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"
