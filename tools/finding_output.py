"""Normalize scanner findings into the public scan-output envelope."""

from __future__ import annotations

from typing import Any

from tools.component_ref import ComponentRef
from tools.graph import Graph
from tools.matcher import Finding
from tools.observations.finding import ObservationFinding
from tools.posture.finding import PostureFinding

_PACKAGE_ECOSYSTEMS = {"npm", "PyPI", "pypi", "docker", "Docker"}


def component_type_for(ref: ComponentRef) -> str:
    extra_type = (ref.extra or {}).get("component_type")
    if isinstance(extra_type, str) and extra_type:
        return extra_type
    if ref.ecosystem in _PACKAGE_ECOSYSTEMS:
        return "package"
    return "component"


def component_name_for(ref: ComponentRef) -> str:
    component_path = (ref.extra or {}).get("component_path")
    if isinstance(component_path, list) and component_path:
        last = component_path[-1]
        if isinstance(last, dict) and isinstance(last.get("name"), str):
            return last["name"]
    if ref.name:
        return ref.name
    if ref.component_identity:
        return ref.component_identity
    return "<unidentified>"


def source_for(ref: ComponentRef) -> dict[str, Any]:
    source: dict[str, Any] = {}
    extra_source = (ref.extra or {}).get("source")
    if isinstance(extra_source, dict):
        source.update({k: v for k, v in extra_source.items() if v is not None})

    has_match_coordinate = bool(ref.ecosystem or ref.purl or source)
    if ref.ecosystem:
        source["ecosystem"] = ref.ecosystem
    if ref.purl:
        source["purl"] = ref.purl
    if has_match_coordinate and ref.name:
        source["name"] = ref.name
    if has_match_coordinate and ref.version:
        source["version"] = ref.version
    external_coord = (ref.extra or {}).get("match_coordinate")
    if isinstance(external_coord, str) and external_coord:
        source["match_coordinate"] = external_coord
    if not source:
        source["status"] = "unknown"
    return source


def declared_by_for(ref: ComponentRef) -> dict[str, Any] | None:
    declared_by = (ref.extra or {}).get("declared_by")
    if isinstance(declared_by, dict):
        return dict(declared_by)
    if ref.source_manifest:
        return {"kind": "manifest", "path": ref.source_manifest}
    return None


def component_path_for(ref: ComponentRef) -> list[dict[str, str]]:
    component_path = (ref.extra or {}).get("component_path")
    if isinstance(component_path, list):
        out = []
        for item in component_path:
            if isinstance(item, dict):
                typ = item.get("type")
                name = item.get("name")
                if isinstance(typ, str) and isinstance(name, str):
                    out.append({"type": typ, "name": name})
        if out:
            return out
    return [{"type": component_type_for(ref), "name": component_name_for(ref)}]


def _component_path_from_graph(ref: ComponentRef, graph: Graph) -> list[dict[str, str]] | None:
    """Derive the component path from graph lineage.

    Walks from the ref's node up to (but not including) the target root and
    returns ancestors in root-to-leaf order. Returns None when the ref has no
    matching node in the graph (flat-BOM / graphless path).
    """
    node = graph.node_for_ref(ref)
    if node is None:
        return None
    lineage = graph.lineage(node)
    # lineage is [node, ..., target_root]; reverse so root ancestors come first,
    # then exclude the target root (ref=None) and any node without a ref.
    path = []
    for ancestor in reversed(lineage):
        if ancestor.ref is None:
            continue
        # component_name_for reads the MCP server's logical name from
        # extra.component_path[-1]["name"] rather than the npm package name.
        path.append({"type": ancestor.kind, "name": component_name_for(ancestor.ref)})
    return path if path else None


def _active_in_for(ref: ComponentRef) -> list[str]:
    active_in = (ref.extra or {}).get("runtime_hosts")
    if isinstance(active_in, list):
        return [v for v in active_in if isinstance(v, str)]
    return []


def _matched_advisory_for(advisory_id: str, advisory: dict | None) -> dict[str, Any]:
    matched: dict[str, Any] = {"id": advisory_id}
    if not isinstance(advisory, dict):
        return matched
    aliases = advisory.get("aliases")
    if isinstance(aliases, list):
        matched["aliases"] = [a for a in aliases if isinstance(a, str)]
    database_specific = advisory.get("database_specific")
    if isinstance(database_specific, dict):
        openaca = database_specific.get("openaca")
        if isinstance(openaca, dict) and isinstance(openaca.get("source"), str):
            matched["source"] = openaca["source"]
    return matched


def finding_to_output(
    finding: Finding, advisory: dict | None, *, graph: Graph | None = None
) -> dict[str, Any]:
    ref = finding.component
    graph_path = _component_path_from_graph(ref, graph) if graph is not None else None
    out: dict[str, Any] = {
        "finding_type": "vulnerability",
        "id": finding.advisory_id,
        "confidence": finding.confidence,
        "title": _title_for_advisory(finding.advisory_id, advisory),
        "component": {
            "type": component_type_for(ref),
            "name": component_name_for(ref),
            "source": source_for(ref),
        },
        "active_in": _active_in_for(ref),
        "component_path": graph_path if graph_path is not None else component_path_for(ref),
        "matched_advisory": _matched_advisory_for(finding.advisory_id, advisory),
    }
    attributed_to = graph.attribution_for_ref(ref) if graph is not None else None
    if attributed_to:
        out["attributed_to"] = attributed_to
    declared_by = declared_by_for(ref)
    if declared_by is not None:
        out["declared_by"] = declared_by
    return out


def posture_to_output(finding: PostureFinding) -> dict[str, Any]:
    out: dict[str, Any] = {
        "finding_type": "posture",
        "source": finding.source,
        "source_version": finding.source_version,
        "rule_id": finding.rule_id,
        "title": finding.title,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "component": finding.component,
        "active_in": finding.active_in,
        "component_path": finding.component_path,
        "standards": finding.standards.to_dict(),
        "remediation": finding.remediation,
        "evidence": finding.evidence,
    }
    if finding.declared_by is not None:
        out["declared_by"] = finding.declared_by
    return out


def observation_to_output(finding: ObservationFinding) -> dict[str, Any]:
    out: dict[str, Any] = {
        "finding_type": "observation",
        "source": finding.source,
        "source_version": finding.source_version,
        "observation_id": finding.observation_id,
        "title": finding.title,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "component": finding.component,
        "subject_coordinate": finding.subject_coordinate,
        "component_path": finding.component_path,
        "categories": finding.categories,
        "evidence": finding.evidence,
    }
    if finding.remediation is not None:
        out["remediation"] = finding.remediation
    if finding.declared_by is not None:
        out["declared_by"] = finding.declared_by
    return out


def _title_for_advisory(advisory_id: str, advisory: dict | None) -> str:
    if isinstance(advisory, dict):
        title = advisory.get("summary") or advisory.get("details")
        if isinstance(title, str) and title:
            return " ".join(title.split())
    return advisory_id
