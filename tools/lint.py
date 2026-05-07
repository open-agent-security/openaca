"""ASVE advisory linter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml
from jsonschema import Draft202012Validator

from tools.cvss import is_valid_cvss_v4

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def find_advisories(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in {".yaml", ".yml"} else []
    return sorted(p for p in target.rglob("*.yaml"))


def check_cvss(advisory: dict) -> list[str]:
    errors: list[str] = []
    for i, sev in enumerate(advisory.get("severity") or []):
        if sev.get("type") == "CVSS_V4":
            score = sev.get("score", "")
            if not is_valid_cvss_v4(score):
                errors.append(f"cvss: severity[{i}].score is not a valid CVSS v4 vector: {score!r}")
    return errors


def check_schema(advisory: dict, validator: Draft202012Validator) -> list[str]:
    errors = []
    for e in validator.iter_errors(advisory):
        path = "/".join(map(str, e.absolute_path)) or "<root>"
        errors.append(f"schema: {e.message} (at {path})")
        # When type-branching rejects exposure/config, surface the type
        # explicitly so contributors see why the record was rejected.
        if e.validator == "not" and isinstance(advisory, dict) and "type" in advisory:
            errors.append(f"schema: type '{advisory['type']}' is reserved; rejected in V0")
    return errors


@click.command()
@click.argument("target", type=click.Path(exists=True, path_type=Path))
def main(target: Path) -> None:
    """Lint ASVE advisories under TARGET (file or directory)."""
    schema = load_schema()
    validator = Draft202012Validator(schema)
    advisories = find_advisories(target)

    if not advisories:
        click.echo(f"no advisory YAML files found under {target}", err=True)
        sys.exit(0)

    failed = 0
    for path in advisories:
        try:
            advisory = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            click.echo(f"{path}: yaml: {e}", err=True)
            failed += 1
            continue

        errors = check_schema(advisory, validator) + check_cvss(advisory)
        if errors:
            failed += 1
            for err in errors:
                click.echo(f"{path}: {err}", err=True)
        else:
            click.echo(f"{path}: ok")

    if failed:
        click.echo(f"\n{failed} of {len(advisories)} advisories failed", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
