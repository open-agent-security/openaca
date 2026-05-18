"""Promote reviewed candidate overlays into the canonical overlay corpus."""

from __future__ import annotations

import datetime
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import click
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tools.lint import UPSTREAM_ID_RE

_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _check_date_time(value: object) -> bool:
    if isinstance(value, str):
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


@_FORMAT_CHECKER.checks("uri", raises=ValueError)
def _check_uri(value: object) -> bool:
    if isinstance(value, str) and not urllib.parse.urlparse(value).scheme:
        raise ValueError(f"not a valid URI: {value!r}")
    return True


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca.schema.json"

CANONICAL_KEYS = ("schema_version", "id", "aliases", "modified", "database_specific")


def project_candidate_to_overlay(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical overlay projection for a reviewed candidate."""
    db = candidate.get("database_specific")
    openaca = (db if isinstance(db, dict) else {}).get("openaca")
    if not isinstance(openaca, dict):
        raise ValueError("candidate must include database_specific.openaca")

    overlay: dict[str, Any] = {}
    for key in CANONICAL_KEYS:
        if key in candidate:
            overlay[key] = candidate[key]
    return overlay


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_overlay(overlay: dict[str, Any]) -> None:
    Draft202012Validator(_load_schema(), format_checker=_FORMAT_CHECKER).validate(overlay)


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
    if not UPSTREAM_ID_RE.match(overlay_id):
        click.echo(f"{overlay_id!r}: not a recognized upstream ID family", err=True)
        sys.exit(1)
    if candidate.resolve() == target.resolve():
        click.echo(f"{candidate}: source and target are the same file", err=True)
        sys.exit(1)
    if target.exists() and not force:
        click.echo(f"{target}: already exists; use --force to overwrite", err=True)
        sys.exit(1)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(overlay, sort_keys=False), encoding="utf-8")
    candidate.unlink()
    click.echo(f"wrote {target}")
    click.echo(f"removed {candidate}")


if __name__ == "__main__":
    main()
