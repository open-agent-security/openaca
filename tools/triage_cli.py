"""`openaca triage` command for exposure reports over scan JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import click

from tools.triage import build_triage_cards
from tools.triage_render import TriageFormat, render_triage_report


@click.command()
@click.argument("scan_json", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--report",
    "report_kind",
    type=click.Choice(["exposure"]),
    default="exposure",
    show_default=True,
    help="Report type to generate.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "markdown", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the rendered report to this file instead of stdout.",
)
def main(
    scan_json: Path,
    report_kind: str,
    output_format: str,
    output_path: Path | None,
) -> None:
    """Triage a structured OpenACA scan JSON artifact."""
    if report_kind != "exposure":
        raise click.ClickException(f"unsupported report type: {report_kind}")
    scan_doc = _read_scan_json(scan_json)
    try:
        cards = build_triage_cards(scan_doc)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    rendered = render_triage_report(
        cards, scan_doc, output_format=cast(TriageFormat, output_format)
    )
    if output_path is None:
        click.echo(rendered, nl=not rendered.endswith("\n"))
        return
    try:
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"failed to write report to {output_path}: {exc}") from exc


def _read_scan_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise click.ClickException(f"{path}: not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"{path}: invalid JSON: {exc.msg}") from exc
    except OSError as exc:
        raise click.ClickException(f"failed to read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"{path}: scan JSON must be an object")
    return data
