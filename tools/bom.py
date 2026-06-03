"""Agent BOM model and CycloneDX serialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from tools.component_ref import ComponentRef

OPENACA_BOM_SCHEMA_VERSION = "0.1"
CYCLONEDX_SPEC_VERSION = "1.7"

_PURL_TO_ECOSYSTEM = {
    "npm": "npm",
    "pypi": "PyPI",
    "github": "GitHub",
    "docker": "Docker",
}


@dataclass(frozen=True)
class BOMComponent:
    ref: ComponentRef
    bom_ref: str


@dataclass(frozen=True)
class BOMEdge:
    parent_bom_ref: str
    child_bom_ref: str


@dataclass(frozen=True)
class AgentBOM:
    components: list[BOMComponent]
    edges: list[BOMEdge]
    target_type: str
    target: str | None = None
    source_unit_count: int | None = None
    source_unit_label: str | None = None

    def component_refs(self) -> list[ComponentRef]:
        return [component.ref for component in self.components]

    def to_cyclonedx(self) -> dict[str, Any]:
        metadata_properties = [
            {"name": "openaca:schema_version", "value": OPENACA_BOM_SCHEMA_VERSION},
            {"name": "openaca:target_type", "value": self.target_type},
        ]
        if self.target is not None:
            metadata_properties.append({"name": "openaca:target", "value": self.target})
        if self.source_unit_count is not None:
            metadata_properties.append(
                {"name": "openaca:source_unit_count", "value": str(self.source_unit_count)}
            )
        if self.source_unit_label is not None:
            metadata_properties.append(
                {"name": "openaca:source_unit_label", "value": self.source_unit_label}
            )

        dependencies: dict[str, list[str]] = {
            component.bom_ref: [] for component in self.components
        }
        for edge in self.edges:
            dependencies[edge.parent_bom_ref].append(edge.child_bom_ref)

        return {
            "bomFormat": "CycloneDX",
            "specVersion": CYCLONEDX_SPEC_VERSION,
            "version": 1,
            "metadata": {
                "tools": [{"vendor": "OpenACA", "name": "openaca"}],
                "properties": metadata_properties,
            },
            "components": [_component_to_cyclonedx(component) for component in self.components],
            "dependencies": [
                {"ref": ref, "dependsOn": depends_on} for ref, depends_on in dependencies.items()
            ],
        }


def build_agent_bom(
    refs: list[ComponentRef],
    *,
    target_type: str,
    target: str | None = None,
    source_unit_count: int | None = None,
    source_unit_label: str | None = None,
) -> AgentBOM:
    components = [
        BOMComponent(ref=ref, bom_ref=bom_ref)
        for ref, bom_ref in zip(refs, _stable_bom_refs(refs), strict=True)
    ]
    return AgentBOM(
        components=components,
        edges=_build_edges(components),
        target_type=target_type,
        target=target,
        source_unit_count=source_unit_count,
        source_unit_label=source_unit_label,
    )


def component_refs_from_cyclonedx(doc: dict[str, Any]) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for component in doc.get("components") or []:
        if not isinstance(component, dict):
            continue
        props = _properties_by_name(component)
        purl = component.get("purl")
        parsed = _parse_purl(purl) if isinstance(purl, str) else {}
        component_identity = props.get("openaca:identity")
        if component_identity is None and not parsed:
            bom_ref = component.get("bom-ref")
            if isinstance(bom_ref, str) and bom_ref:
                component_identity = bom_ref
        refs.append(
            ComponentRef(
                ecosystem=parsed.get("ecosystem"),
                name=parsed.get("name") or _string(component.get("name")),
                version=parsed.get("version") or _string(component.get("version")),
                source_manifest=props.get("openaca:source_manifest") or "",
                source_locator=props.get("openaca:source_locator") or "",
                component_identity=component_identity,
                attributed_to=props.get("openaca:attributed_to"),
                extra=_extra_from_properties(props),
                scope=props.get("openaca:scope") or "agent-component",
            )
        )
    return refs


def target_info_from_cyclonedx(doc: dict[str, Any]) -> tuple[str | None, str | None]:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return None, None
    props = _properties_by_name(metadata)
    return props.get("openaca:target_type"), props.get("openaca:target")


def source_unit_from_cyclonedx(doc: dict[str, Any]) -> tuple[int | None, str | None]:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return None, None
    props = _properties_by_name(metadata)
    count_raw = props.get("openaca:source_unit_count")
    count = int(count_raw) if count_raw is not None and count_raw.isdigit() else None
    return count, props.get("openaca:source_unit_label")


def _stable_bom_refs(refs: list[ComponentRef]) -> list[str]:
    preferred = [_preferred_bom_ref(ref) for ref in refs]
    counts: dict[str, int] = {}
    for value in preferred:
        counts[value] = counts.get(value, 0) + 1
    result: list[str] = []
    for index, (ref, value) in enumerate(zip(refs, preferred, strict=True)):
        if counts[value] == 1:
            result.append(value)
        else:
            result.append(f"{value}#{_short_component_hash(ref, index)}")
    return result


def _preferred_bom_ref(ref: ComponentRef) -> str:
    if ref.purl:
        return ref.purl
    if ref.component_identity:
        return ref.component_identity
    if ref.ecosystem and ref.name:
        if ref.version:
            return f"{ref.ecosystem}:{ref.name}@{ref.version}"
        return f"{ref.ecosystem}:{ref.name}"
    seed = f"{ref.source_manifest}:{ref.source_locator}"
    return f"openaca:unidentified:{_short_hash(seed)}"


def _short_component_hash(ref: ComponentRef, index: int) -> str:
    payload = {
        "index": index,
        "purl": ref.purl,
        "component_identity": ref.component_identity,
        "source_manifest": ref.source_manifest,
        "source_locator": ref.source_locator,
        "component_type": (ref.extra or {}).get("component_type"),
    }
    return _short_hash(json.dumps(payload, sort_keys=True))


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _build_edges(components: list[BOMComponent]) -> list[BOMEdge]:
    # Index by both versionless identity and versioned identity so that
    # attributed_to values like "claude-plugin/mktplace/name@1.0.0" resolve
    # even when the plugin's component_identity is stored without version.
    identity_to_bom_ref: dict[str, str] = {}
    for component in components:
        ci = component.ref.component_identity
        if not ci:
            continue
        identity_to_bom_ref[ci] = component.bom_ref
        if component.ref.version:
            identity_to_bom_ref[f"{ci}@{component.ref.version}"] = component.bom_ref
    edges: list[BOMEdge] = []
    for component in components:
        parent_identity = component.ref.attributed_to
        if not parent_identity:
            continue
        parent_bom_ref = identity_to_bom_ref.get(parent_identity)
        if parent_bom_ref is not None:
            edges.append(BOMEdge(parent_bom_ref=parent_bom_ref, child_bom_ref=component.bom_ref))
    return edges


def _component_to_cyclonedx(component: BOMComponent) -> dict[str, Any]:
    ref = component.ref
    doc: dict[str, Any] = {
        "type": "application",
        "bom-ref": component.bom_ref,
        "name": ref.name or ref.component_identity or "<unidentified>",
    }
    if ref.version:
        doc["version"] = ref.version
    if ref.purl:
        doc["purl"] = ref.purl
    properties = _component_properties(ref)
    if properties:
        doc["properties"] = properties
    return doc


def _component_properties(ref: ComponentRef) -> list[dict[str, str]]:
    props: list[dict[str, str]] = []
    _append_prop(props, "openaca:identity", ref.component_identity)
    _append_prop(props, "openaca:component_type", (ref.extra or {}).get("component_type"))
    _append_prop(props, "openaca:scope", ref.scope)
    _append_prop(props, "openaca:source_manifest", ref.source_manifest)
    _append_prop(props, "openaca:source_locator", ref.source_locator)
    _append_prop(props, "openaca:attributed_to", ref.attributed_to)
    _append_json_prop(props, "openaca:runtime_hosts", (ref.extra or {}).get("runtime_hosts"))
    _append_json_prop(props, "openaca:declared_by", (ref.extra or {}).get("declared_by"))
    _append_json_prop(props, "openaca:component_path", (ref.extra or {}).get("component_path"))
    _append_json_prop(props, "openaca:source", (ref.extra or {}).get("source"))
    _append_prop(props, "openaca:install_source", (ref.extra or {}).get("install_source"))
    _append_prop(
        props,
        "openaca:source_subdirectory",
        (ref.extra or {}).get("source_subdirectory"),
    )
    _append_prop(props, "openaca:git_ref", (ref.extra or {}).get("git_ref"))
    _append_prop(props, "openaca:transport", (ref.extra or {}).get("transport"))
    _append_prop(props, "openaca:url", (ref.extra or {}).get("url"))
    _append_prop(props, "openaca:plugin_scope", (ref.extra or {}).get("scope"))
    _append_prop(props, "openaca:git_commit_sha", (ref.extra or {}).get("gitCommitSha"))
    source_provenance = (ref.extra or {}).get("source_provenance")
    if source_provenance is not None:
        _append_prop(
            props,
            "openaca:source_provenance",
            json.dumps(source_provenance, sort_keys=True),
        )
    return props


def _append_prop(props: list[dict[str, str]], name: str, value: object) -> None:
    if isinstance(value, str) and value:
        props.append({"name": name, "value": value})


def _append_json_prop(props: list[dict[str, str]], name: str, value: object) -> None:
    if value is not None:
        props.append({"name": name, "value": json.dumps(value, sort_keys=True)})


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


def _extra_from_properties(props: dict[str, str]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    component_type = props.get("openaca:component_type")
    if component_type:
        extra["component_type"] = component_type
    for prop_name, extra_key in (
        ("openaca:runtime_hosts", "runtime_hosts"),
        ("openaca:declared_by", "declared_by"),
        ("openaca:component_path", "component_path"),
        ("openaca:source", "source"),
    ):
        value = props.get(prop_name)
        if value:
            try:
                extra[extra_key] = json.loads(value)
            except json.JSONDecodeError:
                extra[extra_key] = value
    for prop_name, extra_key in (
        ("openaca:install_source", "install_source"),
        ("openaca:source_subdirectory", "source_subdirectory"),
        ("openaca:git_ref", "git_ref"),
        ("openaca:transport", "transport"),
        ("openaca:url", "url"),
        ("openaca:plugin_scope", "scope"),
        ("openaca:git_commit_sha", "gitCommitSha"),
    ):
        value = props.get(prop_name)
        if value:
            extra[extra_key] = value
    source_provenance = props.get("openaca:source_provenance")
    if source_provenance:
        try:
            extra["source_provenance"] = json.loads(source_provenance)
        except json.JSONDecodeError:
            extra["source_provenance"] = source_provenance
    return extra


def _parse_purl(purl: str) -> dict[str, str]:
    if not purl.startswith("pkg:"):
        return {}
    body = purl[4:]
    if "/" not in body:
        return {}
    purl_type, remainder = body.split("/", 1)
    ecosystem = _PURL_TO_ECOSYSTEM.get(purl_type)
    if ecosystem is None:
        return {}
    name, _, version = remainder.rpartition("@")
    if not name:
        name = remainder
        version = ""
    # Strip PURL qualifiers (?...) and subpath (#...) per the PURL spec:
    # pkg:type/namespace/name@version?qualifiers#subpath
    if version:
        version = version.split("?", 1)[0].split("#", 1)[0]
    parsed = {"ecosystem": ecosystem, "name": unquote(name)}
    if version:
        parsed["version"] = version
    return parsed


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
