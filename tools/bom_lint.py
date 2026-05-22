"""OpenACA Agent BOM linter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca-bom.schema.json"

_COMPONENT_TYPES = {
    "agent",
    "command",
    "component",
    "hook",
    "mcp_server",
    "plugin",
    "skill",
}
_SCOPES = {"agent-component", "agent-dependency", "software-dependency"}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_bom(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise click.ClickException(f"{path}: not valid UTF-8 — {exc}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"{path}: invalid JSON — {exc}") from exc
    if not isinstance(doc, dict):
        raise click.ClickException(f"{path}: BOM must be a JSON object, got {type(doc).__name__}")
    return doc


def lint_bom(doc: dict[str, Any], validator: Draft202012Validator) -> list[str]:
    return check_schema(doc, validator) + check_semantics(doc)


def check_schema(doc: dict[str, Any], validator: Draft202012Validator) -> list[str]:
    errors: list[str] = []
    for error in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        path = "/".join(map(str, error.absolute_path)) or "<root>"
        errors.append(f"schema: {error.message} (at {path})")
    return errors


def check_semantics(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    components = [c for c in doc.get("components") or [] if isinstance(c, dict)]
    bom_refs: set[str] = set()
    duplicate_refs: set[str] = set()
    for component in components:
        bom_ref = component.get("bom-ref")
        if not isinstance(bom_ref, str):
            continue
        if bom_ref in bom_refs:
            duplicate_refs.add(bom_ref)
        bom_refs.add(bom_ref)
    for bom_ref in sorted(duplicate_refs):
        errors.append(f"duplicate bom-ref {bom_ref!r}")

    for index, component in enumerate(components):
        errors.extend(_check_component(component, index))

    for index, dependency in enumerate(doc.get("dependencies") or []):
        if not isinstance(dependency, dict):
            continue
        ref = dependency.get("ref")
        if isinstance(ref, str) and ref not in bom_refs:
            errors.append(f"dependencies[{index}].ref {ref!r} does not match any component bom-ref")
        for target in dependency.get("dependsOn") or []:
            if isinstance(target, str) and target not in bom_refs:
                errors.append(
                    f"dependencies[{index}]: dependency target {target!r} "
                    "does not match any component bom-ref"
                )
    return errors


def _check_component(component: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    props = _properties_by_name(component)
    if not component.get("purl") and "openaca:identity" not in props:
        errors.append(f"components[{index}] must have either purl or openaca:identity")

    component_type = props.get("openaca:component_type")
    if component_type is not None and component_type not in _COMPONENT_TYPES:
        errors.append(
            f"components[{index}]: openaca:component_type {component_type!r} is not recognized"
        )

    scope = props.get("openaca:scope")
    if scope is not None and scope not in _SCOPES:
        errors.append(f"components[{index}]: openaca:scope {scope!r} is not recognized")
    return errors


def _properties_by_name(component: dict[str, Any]) -> dict[str, str]:
    props: dict[str, str] = {}
    for prop in component.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        value = prop.get("value")
        if isinstance(name, str) and isinstance(value, str):
            props[name] = value
    return props


@click.command(name="lint")
@click.argument("target", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def main(target: Path) -> None:
    """Lint an OpenACA Agent BOM JSON file."""
    schema = load_schema()
    validator = Draft202012Validator(schema)
    doc = load_bom(target)
    errors = lint_bom(doc, validator)
    if errors:
        for error in errors:
            click.echo(f"{target}: {error}", err=True)
        sys.exit(1)
    click.echo(f"{target}: ok")


if __name__ == "__main__":
    main()
