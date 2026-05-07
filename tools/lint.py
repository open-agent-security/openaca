"""ASVE advisory linter."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click
import yaml
from jsonschema import Draft202012Validator

from tools.cvss import is_valid_cvss_v4

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"
ID_RE = re.compile(r"^ASVE-(\d{4})-\d{4}$")


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def find_advisories(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in {".yaml", ".yml"} else []
    return sorted(p for p in target.rglob("*.yaml"))


YEAR_DIR_RE = re.compile(r"^\d{4}$")


def check_path_consistency(advisory: dict, path: Path) -> list[str]:
    # Only apply to files that look like real advisories (parent is a 4-digit
    # year directory). Fixtures and other YAML files outside the advisories
    # tree are exempt — they intentionally don't conform to the layout.
    if not YEAR_DIR_RE.match(path.parent.name):
        return []
    advisory_id = advisory.get("id", "")
    m = ID_RE.match(advisory_id)
    if not m:
        return []  # schema check covers this
    year = m.group(1)
    expected_filename = f"{advisory_id}.yaml"
    parent_year = path.parent.name
    errors: list[str] = []
    if path.name != expected_filename:
        errors.append(f"path: filename {path.name!r} should be {expected_filename!r}")
    if parent_year != year:
        errors.append(f"path: parent dir {parent_year!r} should be {year!r}")
    return errors


def check_cvss(advisory: dict) -> list[str]:
    errors: list[str] = []
    for i, sev in enumerate(advisory.get("severity") or []):
        if sev.get("type") == "CVSS_V4":
            score = sev.get("score", "")
            if not is_valid_cvss_v4(score):
                errors.append(f"cvss: severity[{i}].score is not a valid CVSS v4 vector: {score!r}")
    return errors


def check_internal_aliases(advisory: dict, known_ids: set[str]) -> list[str]:
    errors: list[str] = []
    for alias in advisory.get("aliases") or []:
        if isinstance(alias, str) and ID_RE.match(alias):
            if alias != advisory.get("id") and alias not in known_ids:
                errors.append(f"alias: ASVE alias {alias!r} not found in corpus")
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

    # Pass 1: load all advisories; collect known IDs.
    loaded: list[tuple[Path, dict | None, str | None]] = []
    known_ids: set[str] = set()
    for path in advisories:
        try:
            advisory = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            loaded.append((path, None, f"yaml: {e}"))
            continue
        loaded.append((path, advisory, None))
        if isinstance(advisory, dict) and isinstance(advisory.get("id"), str):
            known_ids.add(advisory["id"])

    # Pass 2: per-advisory checks.
    failed = 0
    for path, advisory, parse_error in loaded:
        if parse_error:
            click.echo(f"{path}: {parse_error}", err=True)
            failed += 1
            continue
        errors = (
            check_schema(advisory, validator)
            + check_cvss(advisory)
            + check_path_consistency(advisory, path)
            + check_internal_aliases(advisory, known_ids)
        )
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
