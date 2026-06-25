"""Diff CycloneDX Agent BOM component occurrences and composition edges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class BomDiffComponent:
    bom_ref: str
    identity: str | None
    component_type: str | None
    name: str | None
    version: str | None
    purl: str | None
    git_commit_sha: str | None
    artifact_coordinates: str | None
    url: str | None
    install_source: str | None
    git_ref: str | None
    transport: str | None
    source_provenance: str | None
    match_coordinate: str | None

    def comparable(self) -> tuple[str | None, ...]:
        return (
            self.identity,
            self.component_type,
            self.name,
            self.version,
            self.purl,
            self.git_commit_sha,
            self.artifact_coordinates,
            self.url,
            self.install_source,
            self.git_ref,
            self.transport,
            self.source_provenance,
            self.match_coordinate,
        )

    def to_json(self) -> JsonObject:
        return {
            "bom_ref": self.bom_ref,
            "identity": self.identity,
            "component_type": self.component_type,
            "name": self.name,
            "version": self.version,
            "purl": self.purl,
            "git_commit_sha": self.git_commit_sha,
            "artifact_coordinates": self.artifact_coordinates,
            "url": self.url,
            "install_source": self.install_source,
            "git_ref": self.git_ref,
            "transport": self.transport,
            "source_provenance": self.source_provenance,
            "match_coordinate": self.match_coordinate,
        }


@dataclass(frozen=True)
class ChangedBomDiffComponent:
    before: BomDiffComponent
    after: BomDiffComponent

    def to_json(self) -> JsonObject:
        return {
            "bom_ref": self.after.bom_ref,
            "identity": self.after.identity,
            "component_type": self.after.component_type,
            "name": self.after.name,
            "before": {
                "version": self.before.version,
                "purl": self.before.purl,
                "git_commit_sha": self.before.git_commit_sha,
                "artifact_coordinates": self.before.artifact_coordinates,
                "url": self.before.url,
                "install_source": self.before.install_source,
                "git_ref": self.before.git_ref,
                "transport": self.before.transport,
                "source_provenance": self.before.source_provenance,
                "match_coordinate": self.before.match_coordinate,
            },
            "after": {
                "version": self.after.version,
                "purl": self.after.purl,
                "git_commit_sha": self.after.git_commit_sha,
                "artifact_coordinates": self.after.artifact_coordinates,
                "url": self.after.url,
                "install_source": self.after.install_source,
                "git_ref": self.after.git_ref,
                "transport": self.after.transport,
                "source_provenance": self.after.source_provenance,
                "match_coordinate": self.after.match_coordinate,
            },
        }


@dataclass(frozen=True)
class BomDiffResult:
    added_components: list[BomDiffComponent]
    removed_components: list[BomDiffComponent]
    changed_components: list[ChangedBomDiffComponent]
    added_edges: list[tuple[str, str]]
    removed_edges: list[tuple[str, str]]

    def to_json(self) -> JsonObject:
        return {
            "added_components": [c.to_json() for c in self.added_components],
            "removed_components": [c.to_json() for c in self.removed_components],
            "changed_components": [c.to_json() for c in self.changed_components],
            "added_edges": [_edge_to_json(edge) for edge in self.added_edges],
            "removed_edges": [_edge_to_json(edge) for edge in self.removed_edges],
        }


def diff_boms(before: Any, after: Any) -> BomDiffResult:
    before_doc = _require_object(before, "before")
    after_doc = _require_object(after, "after")
    before_components = _components_by_bom_ref(before_doc)
    after_components = _components_by_bom_ref(after_doc)
    before_refs = set(before_components)
    after_refs = set(after_components)
    shared_refs = before_refs & after_refs

    changed = [
        ChangedBomDiffComponent(before_components[bom_ref], after_components[bom_ref])
        for bom_ref in sorted(shared_refs)
        if before_components[bom_ref].comparable() != after_components[bom_ref].comparable()
    ]
    before_edges = _edges(before_doc)
    after_edges = _edges(after_doc)

    return BomDiffResult(
        added_components=[after_components[r] for r in sorted(after_refs - before_refs)],
        removed_components=[before_components[r] for r in sorted(before_refs - after_refs)],
        changed_components=changed,
        added_edges=sorted(after_edges - before_edges),
        removed_edges=sorted(before_edges - after_edges),
    )


def _require_object(value: Any, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} BOM must be a JSON object")
    return value


def _components_by_bom_ref(doc: JsonObject) -> dict[str, BomDiffComponent]:
    components = doc.get("components")
    if components is None:
        return {}
    if not isinstance(components, list):
        raise ValueError("BOM components must be an array")
    result: dict[str, BomDiffComponent] = {}
    for item in components:
        if not isinstance(item, dict):
            continue
        bom_ref = item.get("bom-ref")
        if not isinstance(bom_ref, str) or not bom_ref:
            continue
        result[bom_ref] = BomDiffComponent(
            bom_ref=bom_ref,
            identity=_property(item, "openaca:identity"),
            component_type=_property(item, "openaca:component_type"),
            name=_string_or_none(item.get("name")),
            version=_string_or_none(item.get("version")),
            purl=_string_or_none(item.get("purl")),
            git_commit_sha=_property(item, "openaca:git_commit_sha"),
            artifact_coordinates=_property(item, "openaca:artifact_coordinates"),
            url=_property(item, "openaca:url"),
            install_source=_property(item, "openaca:install_source"),
            git_ref=_property(item, "openaca:git_ref"),
            transport=_property(item, "openaca:transport"),
            source_provenance=_property(item, "openaca:source_provenance"),
            match_coordinate=_property(item, "openaca:match_coordinate"),
        )
    return result


def _edges(doc: JsonObject) -> set[tuple[str, str]]:
    dependencies = doc.get("dependencies")
    if dependencies is None:
        return set()
    if not isinstance(dependencies, list):
        raise ValueError("BOM dependencies must be an array")
    result: set[tuple[str, str]] = set()
    for item in dependencies:
        if not isinstance(item, dict):
            continue
        parent = item.get("ref")
        children = item.get("dependsOn")
        if not isinstance(parent, str) or not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, str):
                result.add((parent, child))
    return result


def _property(component: JsonObject, name: str) -> str | None:
    properties = component.get("properties")
    if not isinstance(properties, list):
        return None
    for prop in properties:
        if (
            isinstance(prop, dict)
            and prop.get("name") == name
            and isinstance(prop.get("value"), str)
        ):
            return prop["value"]
    return None


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _edge_to_json(edge: tuple[str, str]) -> JsonObject:
    parent, child = edge
    return {"parent": parent, "child": child}
