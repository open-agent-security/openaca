"""CLI for validating and compiling endpoint policy artifacts.

The compilation itself lives in `tools/policy_compile.py`, below the command
layer. What stays here is the command's own: the `--output` pre-check that
keeps a usage error's exit code 2, the `PolicyValidationError` /
`PolicyEvaluationError` translation into a `click.ClickException`, the
`click.echo` of the rendered report, and the `--project`-omitted note.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from tools.policy import (
    PolicyEvaluationError,
    PolicyValidationError,
    load,
)
from tools.policy_compile import compile_endpoint_policy, render_policy_report


@click.group()
def main() -> None:
    """Validate and compile restrictive endpoint policies."""


@main.command()
@click.argument("policy_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def validate(policy_path: Path) -> None:
    """Validate a policy document without scanning an endpoint."""
    try:
        load(policy_path)
    except PolicyValidationError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("policy_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--target", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--project", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--host", type=click.Choice(["claude"]), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--managed-settings-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text")
def compile(
    policy_path: Path,
    target: Path,
    project: Path | None,
    host: str,
    output: Path | None,
    managed_settings_dir: Path | None,
    dry_run: bool,
    output_format: str,
) -> None:
    """Scan one endpoint and render its host-managed policy artifact."""
    if output is None and not dry_run:
        raise click.UsageError("--output is required unless --dry-run is set")
    try:
        policy = load(policy_path)
        compilation = compile_endpoint_policy(
            policy,
            target=target,
            project=project,
            output=output,
            managed_settings_dir=managed_settings_dir,
            dry_run=dry_run,
        )
    except (PolicyValidationError, PolicyEvaluationError) as exc:
        raise click.ClickException(str(exc)) from exc

    emit_policy_report(compilation, output_format)
    if project is None:
        click.echo(
            "note: project-local configuration was not scanned; pass --project to include it",
            err=True,
        )


def emit_policy_report(report: dict[str, Any], output_format: str) -> None:
    click.echo(render_policy_report(report, output_format))
