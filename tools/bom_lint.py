"""OpenACA Agent BOM linter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from jsonschema import Draft202012Validator

from tools.agent_kinds import REGISTRY
from tools.bom import AGENT_ROOT_PREFIX
from tools.capability import COVERAGE_LEVELS, Capability

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca-bom.schema.json"

_COMPONENT_TYPES = {
    "agent",
    "command",
    "component",
    "hook",
    "mcp_server",
    "package",
    "plugin",
    "skill",
}
_SCOPES = {"agent-component", "agent-dependency", "software-dependency"}
_COMPOSITION_SOURCES = {"installed", "declared"}


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

    # The scan target is encoded as `metadata.component` (not a `components[]`
    # entry) and is a valid dependency endpoint: graph-backed BOMs emit edges
    # whose parent is the target's bom-ref (e.g. `openaca:target`). Accept it.
    metadata = doc.get("metadata")
    if isinstance(metadata, dict):
        metadata_component = metadata.get("component")
        if isinstance(metadata_component, dict):
            metadata_ref = metadata_component.get("bom-ref")
            if isinstance(metadata_ref, str):
                bom_refs.add(metadata_ref)

    schema_version = _openaca_schema_version(doc)
    for index, component in enumerate(components):
        errors.extend(_check_component(component, index, schema_version=schema_version))

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

    errors.extend(check_agent_metadata(doc))
    if isinstance(metadata, dict) and isinstance(metadata.get("component"), dict):
        errors.extend(
            _check_duplicate_openaca_properties(metadata["component"], "metadata.component")
        )
    for index, component in enumerate(components):
        errors.extend(_check_duplicate_openaca_properties(component, f"components[{index}]"))
    return errors


def check_agent_metadata(doc: dict[str, Any]) -> list[str]:
    """Invariants for an agent-rooted document.

    The gate is `metadata.component`'s bom-ref prefix (ADR-0045), not
    `openaca:target_type` — which agent BOMs no longer carry.
    """
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return []
    component = metadata.get("component")
    if not isinstance(component, dict):
        return []
    bom_ref = component.get("bom-ref")
    if not isinstance(bom_ref, str) or not bom_ref.startswith(AGENT_ROOT_PREFIX):
        return []

    errors: list[str] = []
    props = _properties_by_name(component)

    agent_kind = props.get("openaca:agent_kind")
    if not agent_kind:
        errors.append("metadata.component: openaca:agent_kind is required on an agent BOM")

    coverage = props.get("openaca:composition_coverage")
    if coverage not in COVERAGE_LEVELS:
        errors.append(
            "metadata.component: openaca:composition_coverage must be one of "
            f"{sorted(COVERAGE_LEVELS)}, got {coverage!r}"
        )

    source = props.get("openaca:composition_source")
    if source not in _COMPOSITION_SOURCES:
        errors.append(
            "metadata.component: openaca:composition_source must be one of "
            f"{sorted(_COMPOSITION_SOURCES)}, got {source!r}"
        )

    if agent_kind:
        agent_id = props.get("openaca:agent_id")
        expected = (
            f"{AGENT_ROOT_PREFIX}{agent_kind}"
            if agent_id is None
            else f"{AGENT_ROOT_PREFIX}{agent_kind}/{agent_id}"
        )
        if bom_ref != expected:
            errors.append(
                f"metadata.component: bom-ref {bom_ref!r} is inconsistent with "
                f"openaca:agent_kind/openaca:agent_id (expected {expected!r})"
            )

        cardinality = _kind_cardinality(agent_kind)
        if cardinality == "singleton" and agent_id is not None:
            errors.append(
                f"metadata.component: kind {agent_kind!r} is singleton; "
                "openaca:agent_id must be absent"
            )
        elif cardinality == "many_per_place" and not agent_id:
            errors.append(
                f"metadata.component: kind {agent_kind!r} has same-kind multiplicity; "
                "openaca:agent_id is required"
            )
    return errors


def _kind_cardinality(agent_kind: str) -> str | None:
    """`None` means the kind is unknown to this build's registry — third-party
    kinds this scanner has never registered are not a lint failure; there is
    nothing to check the discriminator against."""
    for kind in REGISTRY:
        if kind.id == agent_kind:
            return kind.cardinality
    return None


def _check_duplicate_openaca_properties(component: dict[str, Any], label: str) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for prop in component.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        if not isinstance(name, str) or not name.startswith("openaca:"):
            continue
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return [f"{label}: {name!r} appears more than once" for name in sorted(duplicates)]


def _check_component(
    component: dict[str, Any],
    index: int,
    *,
    schema_version: str | None,
) -> list[str]:
    errors: list[str] = []
    props = _properties_by_name(component)
    if schema_version in {"0.1", "0.2", "0.3"} and "openaca:identity" not in props:
        errors.append(f"components[{index}] must have openaca:identity")

    component_type = props.get("openaca:component_type")
    if component_type is not None and component_type not in _COMPONENT_TYPES:
        errors.append(
            f"components[{index}]: openaca:component_type {component_type!r} is not recognized"
        )

    scope = props.get("openaca:scope")
    if scope is not None and scope not in _SCOPES:
        errors.append(f"components[{index}]: openaca:scope {scope!r} is not recognized")

    errors.extend(_check_capability_descriptors(props, index))
    return errors


def _openaca_schema_version(doc: dict[str, Any]) -> str | None:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return _properties_by_name(metadata).get("openaca:schema_version")


def _check_capability_descriptors(props: dict[str, str], index: int) -> list[str]:
    errors: list[str] = []
    coverage = props.get("openaca:capability_coverage")
    if coverage is not None and coverage not in COVERAGE_LEVELS:
        errors.append(
            f"components[{index}]: openaca:capability_coverage {coverage!r} is not recognized"
        )

    capabilities_raw = props.get("openaca:capabilities")
    if (capabilities_raw is None) != (coverage is None):
        errors.append(
            f"components[{index}]: openaca:capabilities and openaca:capability_coverage "
            "must both be present or both absent"
        )
    if capabilities_raw is None:
        return errors
    try:
        capabilities = json.loads(capabilities_raw)
    except json.JSONDecodeError:
        errors.append(f"components[{index}]: openaca:capabilities is not valid JSON")
        return errors
    if not isinstance(capabilities, list):
        errors.append(f"components[{index}]: openaca:capabilities must be a JSON array")
        return errors
    for entry_index, entry in enumerate(capabilities):
        try:
            Capability.from_dict(entry)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                f"components[{index}]: openaca:capabilities[{entry_index}] is invalid: {exc}"
            )
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
