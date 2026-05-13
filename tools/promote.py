"""Promote reviewed candidate overlays into the canonical overlay corpus."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"

CANONICAL_KEYS = ("schema_version", "id", "aliases", "modified", "database_specific")


def project_candidate_to_overlay(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical overlay projection for a reviewed candidate."""
    asve = (candidate.get("database_specific") or {}).get("asve")
    if not isinstance(asve, dict):
        raise ValueError("candidate must include database_specific.asve")

    overlay: dict[str, Any] = {}
    for key in CANONICAL_KEYS:
        if key in candidate:
            overlay[key] = candidate[key]
    return overlay


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_overlay(overlay: dict[str, Any]) -> None:
    Draft202012Validator(_load_schema()).validate(overlay)


@click.command()
@click.argument("candidate", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--overlays",
    "overlays_dir",
    default=REPO_ROOT / "overlays",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Canonical overlay corpus directory.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing canonical overlay.")
def main(candidate: Path, overlays_dir: Path, force: bool) -> None:
    """Promote a reviewed candidate YAML file into overlays/<id>.yaml."""
    try:
        data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("candidate YAML must contain a mapping")
        overlay = project_candidate_to_overlay(data)
        _validate_overlay(overlay)
    except Exception as exc:
        click.echo(f"{candidate}: {exc}", err=True)
        sys.exit(1)

    overlay_id = overlay["id"]
    target = overlays_dir / f"{overlay_id}.yaml"
    if target.resolve().parent != overlays_dir.resolve():
        click.echo(f"{overlay_id!r}: unsafe overlay ID", err=True)
        sys.exit(1)
    if target.exists() and not force:
        click.echo(f"{target}: already exists; use --force to overwrite", err=True)
        sys.exit(1)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(overlay, sort_keys=False), encoding="utf-8")
    click.echo(f"wrote {target}")


if __name__ == "__main__":
    main()
