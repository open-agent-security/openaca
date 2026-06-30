"""Agent BOM model and CycloneDX serialization.

V1 invariant: a graph node's `key` IS the CycloneDX `bom-ref`. When a `Graph`
is supplied, every `components[]` entry's `bom-ref` and every `dependencies[]`
`ref`/`dependsOn` value is the corresponding node's `key` (the normalized
occurrence identity from `graph_build`). `bom.py` cannot recompute occurrence
keys — it lacks the source normalizer — so the key must come from the node;
graph-backed components never go through `_preferred_bom_ref`. The synthetic
target root (`graph.root`, `ref is None`) becomes `metadata.component` with its
stable logical bom-ref (`openaca:target`) and is not added to `components[]`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import unquote

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.graph import Edge, Graph, Node
from tools.identity import infer_unpinned_mcp_package, match_coordinate_for_bom

OPENACA_BOM_SCHEMA_VERSION = "0.2"
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
    target_bom_ref: str | None = None

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

        # dependencies[] keys: every component, plus the target root (when graph-
        # backed) so edges whose parent is the target have a node to hang from.
        dependencies: dict[str, list[str]] = {
            component.bom_ref: [] for component in self.components
        }
        if self.target_bom_ref is not None:
            dependencies.setdefault(self.target_bom_ref, [])
        for edge in self.edges:
            dependencies.setdefault(edge.parent_bom_ref, []).append(edge.child_bom_ref)

        metadata: dict[str, Any] = {
            "tools": [{"vendor": "OpenACA", "name": "openaca"}],
            "properties": metadata_properties,
        }
        if self.target_bom_ref is not None:
            metadata["component"] = {
                "type": "application",
                "bom-ref": self.target_bom_ref,
                "name": self.target or self.target_bom_ref,
                "properties": [{"name": "openaca:component_type", "value": "target"}],
            }

        return {
            "bomFormat": "CycloneDX",
            "specVersion": CYCLONEDX_SPEC_VERSION,
            "version": 1,
            "metadata": metadata,
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
    graph: Graph | None = None,
) -> AgentBOM:
    if graph is not None:
        return _build_agent_bom_from_graph(
            graph,
            target_type=target_type,
            target=target,
            source_unit_count=source_unit_count,
            source_unit_label=source_unit_label,
        )
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


_AGENT_SCOPES = frozenset({"agent-component", "agent-dependency"})


def _build_agent_bom_from_graph(
    graph: Graph,
    *,
    target_type: str,
    target: str | None,
    source_unit_count: int | None,
    source_unit_label: str | None,
) -> AgentBOM:
    """Encode the composition graph: node.key == bom-ref (the V1 invariant).

    Components are the graph's non-root nodes restricted to agent scope (matching
    today's BOM, which excludes software-dependency). Each component's content
    comes from `node.ref` with the graph-derived `scope` stamped on (so
    `component_refs()` keeps feeding render/matcher correctly), and its bom-ref
    is `node.key` — never `_preferred_bom_ref`. Attribution lives in
    `dependencies[]` (the graph edges), not on the component. The target is
    `metadata.component`, not a `components[]` entry; `dependencies[]` are the
    graph's edges restricted to included endpoints.
    """
    root = graph.root
    included: dict[str, BOMComponent] = {}
    for node in graph.nodes.values():
        if node.ref is None:  # synthetic target root: never a components[] entry
            continue
        if graph.scope_of(node) not in _AGENT_SCOPES:
            continue
        ref = replace(node.ref, scope=graph.scope_of(node))
        included[node.key] = BOMComponent(ref=ref, bom_ref=node.key)

    included_keys = set(included) | {root.key}
    edges = [
        BOMEdge(parent_bom_ref=e.parent, child_bom_ref=e.child)
        for e in graph.edges
        if e.parent in included_keys and e.child in included_keys
    ]
    return AgentBOM(
        components=list(included.values()),
        edges=edges,
        target_type=target_type,
        target=target,
        source_unit_count=source_unit_count,
        source_unit_label=source_unit_label,
        target_bom_ref=root.key,
    )


def bom_components_from_cyclonedx(doc: dict[str, Any]) -> list[BOMComponent]:
    """Reconstruct components paired with their CycloneDX `bom-ref`.

    Consumers persist findings against their stored rows by `bom-ref`, so they
    need the ref alongside each reconstructed `ComponentRef`. Components emitted
    by `build_agent_bom` always carry a `bom-ref`; a positional fallback covers
    any externally-produced doc that omits it.
    """
    components: list[BOMComponent] = []
    for index, component in enumerate(doc.get("components") or []):
        if not isinstance(component, dict):
            continue
        ref = _component_ref_from_cyclonedx(component)
        bom_ref = component.get("bom-ref")
        if not (isinstance(bom_ref, str) and bom_ref):
            bom_ref = f"component-{index}"
        components.append(BOMComponent(ref=ref, bom_ref=bom_ref))
    return components


def component_refs_from_cyclonedx(doc: dict[str, Any]) -> list[ComponentRef]:
    return [component.ref for component in bom_components_from_cyclonedx(doc)]


_SYNTHETIC_TARGET_KEY = "openaca:target"


def graph_from_cyclonedx(doc: dict[str, Any]) -> Graph:
    """Reconstruct the composition graph from a CycloneDX Agent BOM.

    The flat ref list cannot carry edges, so a BOM consumer that needs scope or
    attribution (which are graph-derived) must rebuild the graph. The V1
    invariant (`node.key == bom-ref`) makes this lossless for the agent-scope
    subtree a graph-backed BOM encodes:

    - `metadata.component` → the target root node (`kind="target"`, `ref=None`).
      A BOM without one (a flat/externally-produced BOM with no edges) gets a
      synthetic `openaca:target` root so the graph still validates.
    - each `components[]` entry → a node keyed by its `bom-ref`, `kind` from the
      `openaca:component_type` property (packages omit a distinct type, so
      default to `"package"`), `ref` reconstructed from the component.
    - `dependencies[]` → an `Edge(parent, child)` per `dependsOn` entry.
    - any component with no parent in `dependencies[]` is attached as a direct
      child of the target. This is a no-op for a graph-backed BOM (its
      top-level components already carry an explicit target→child edge) and
      makes a flat BOM (no edges) reconstruct as `target → each component`.

    The synthesized flat graph (no `metadata.component`) is structural only: a
    top-level `package` node attached directly under the target has no agent
    ancestor, so `scope_of` re-derives it as `software-dependency` regardless of
    the stored `openaca:scope`. Flat BOMs therefore do NOT round-trip scope
    through this function; `scan_bom` preserves their classification by reading
    the stored `openaca:scope` (via `component_refs_from_cyclonedx`) and only
    uses this graph for the genuinely graph-backed case.

    `validate()` runs before returning. Software-dependency nodes were excluded
    from `components[]` at emit time, so the reconstructed graph is the
    agent-scope projection; every remaining node still has a path to the target.
    """
    metadata = doc.get("metadata")
    target_component = metadata.get("component") if isinstance(metadata, dict) else None
    target_ref: str | None = None
    if isinstance(target_component, dict):
        ref = target_component.get("bom-ref")
        if isinstance(ref, str) and ref:
            target_ref = ref
    if target_ref is None:
        target_ref = _SYNTHETIC_TARGET_KEY

    nodes: dict[str, Node] = {target_ref: Node(key=target_ref, kind="target", ref=None)}
    for component in doc.get("components") or []:
        if not isinstance(component, dict):
            continue
        bom_ref = component.get("bom-ref")
        if not (isinstance(bom_ref, str) and bom_ref):
            continue
        if bom_ref == target_ref:
            continue
        props = _properties_by_name(component)
        kind = props.get("openaca:component_type") or "package"
        nodes[bom_ref] = Node(key=bom_ref, kind=kind, ref=_component_ref_from_cyclonedx(component))

    edges: list[Edge] = []
    has_parent: set[str] = set()
    for dependency in doc.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        parent = dependency.get("ref")
        if not isinstance(parent, str):
            continue
        if parent not in nodes:
            # A foreign/unknown parent ref can't anchor an edge (it would dangle
            # and crash validate()). Skip it so the child falls back to target.
            continue
        for child in dependency.get("dependsOn") or []:
            if isinstance(child, str) and child and child != parent and child in nodes:
                edges.append(Edge(parent=parent, child=child))
                has_parent.add(child)

    for key in nodes:
        if key != target_ref and key not in has_parent:
            edges.append(Edge(parent=target_ref, child=key))

    graph = Graph(nodes=nodes, edges=edges)
    graph.validate()
    return graph


def _component_ref_from_cyclonedx(component: dict[str, Any]) -> ComponentRef:
    props = _properties_by_name(component)
    purl = component.get("purl")
    parsed = _parse_purl(purl) if isinstance(purl, str) else {}
    extra = _extra_from_properties(props)
    if not parsed:
        inferred_package = infer_unpinned_mcp_package(extra)
        if inferred_package is not None:
            ecosystem, name = inferred_package
            parsed = {"ecosystem": ecosystem, "name": name}
    component_identity = props.get("openaca:identity")
    if component_identity is None and not parsed:
        bom_ref = component.get("bom-ref")
        if isinstance(bom_ref, str) and bom_ref:
            component_identity = bom_ref
    return ComponentRef(
        ecosystem=parsed.get("ecosystem"),
        name=parsed.get("name") or _string(component.get("name")),
        version=parsed.get("version") or _string(component.get("version")),
        source_manifest=props.get("openaca:source_manifest") or "",
        source_locator=props.get("openaca:source_locator") or "",
        component_identity=component_identity,
        extra=extra,
        scope=props.get("openaca:scope") or "agent-component",
    )


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
    identity = canonical_component_identity(ref)
    if identity:
        return identity
    if ref.purl:
        return ref.purl
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
        "component_identity": canonical_component_identity(ref),
        "source_manifest": ref.source_manifest,
        "source_locator": ref.source_locator,
        "component_type": (ref.extra or {}).get("component_type"),
    }
    return _short_hash(json.dumps(payload, sort_keys=True))


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _build_edges(components: list[BOMComponent]) -> list[BOMEdge]:
    # Composition edges come from the graph. A flat ref list (the `graph is None`
    # path) carries no composition information now that `attributed_to` is gone,
    # so a graphless BOM has no edges. Callers that need edges pass a `Graph`.
    return []


def _component_to_cyclonedx(component: BOMComponent) -> dict[str, Any]:
    ref = component.ref
    identity = canonical_component_identity(ref)
    doc: dict[str, Any] = {
        "type": _cyclonedx_component_type(ref),
        "bom-ref": component.bom_ref,
        "name": ref.name or identity or ref.component_identity or "<unidentified>",
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
    identity = canonical_component_identity(ref)
    _append_prop(props, "openaca:identity", identity)
    _append_prop(props, "openaca:match_coordinate", match_coordinate_for_bom(ref))
    _append_prop(props, "openaca:component_type", _openaca_component_type(ref))
    _append_prop(props, "openaca:scope", ref.scope)
    _append_prop(props, "openaca:source_manifest", ref.source_manifest)
    _append_prop(props, "openaca:source_locator", ref.source_locator)
    _append_prop(props, "openaca:agent_host", _agent_host(ref))
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
    _append_json_prop(
        props,
        "openaca:artifact_coordinates",
        (ref.extra or {}).get("artifact_coordinates"),
    )
    source_provenance = (ref.extra or {}).get("source_provenance")
    if source_provenance is not None:
        _append_prop(
            props,
            "openaca:source_provenance",
            json.dumps(source_provenance, sort_keys=True),
        )
    return props


def _cyclonedx_component_type(ref: ComponentRef) -> str:
    return "library" if _is_package_dependency(ref) else "application"


def _openaca_component_type(ref: ComponentRef) -> str | None:
    component_type = (ref.extra or {}).get("component_type")
    if isinstance(component_type, str) and component_type:
        return component_type
    return "package" if _is_package_dependency(ref) else None


def _is_package_dependency(ref: ComponentRef) -> bool:
    return ref.scope == "agent-dependency" and ref.purl is not None


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
    match_coordinate = props.get("openaca:match_coordinate")
    if match_coordinate:
        extra["match_coordinate"] = match_coordinate
    agent_host = props.get("openaca:agent_host")
    if agent_host:
        extra["agent_host"] = agent_host
    for prop_name, extra_key in (
        ("openaca:runtime_hosts", "runtime_hosts"),
        ("openaca:declared_by", "declared_by"),
        ("openaca:component_path", "component_path"),
        ("openaca:source", "source"),
        ("openaca:artifact_coordinates", "artifact_coordinates"),
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


def _agent_host(ref: ComponentRef) -> str | None:
    runtime_hosts = (ref.extra or {}).get("runtime_hosts")
    if not isinstance(runtime_hosts, list) or len(runtime_hosts) != 1:
        return None
    value = runtime_hosts[0]
    return value if isinstance(value, str) and value else None


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
