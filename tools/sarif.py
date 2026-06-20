"""Render OpenACA findings as SARIF v2.1.0.

SARIF (Static Analysis Results Interchange Format) is the format
GitHub's code-scanning UI ingests. The Action emits a SARIF document
so findings render inline on PRs at the manifest line that referenced
the vulnerable component.

Confidence → SARIF level mapping:
- high     → error    (concrete version in vulnerable range)
- low      → warning  (range/spec like ^1.0.0; consumer should pin)
- unknown  → note     (unpinned npx/uvx launch matched a known
                       vulnerable package; no version to compare)
"""

from __future__ import annotations

from typing import Any

from tools.finding_output import finding_to_output, observation_to_output, posture_to_output
from tools.graph import Graph
from tools.matcher import Finding
from tools.observations.finding import ObservationFinding
from tools.posture.finding import PostureFinding

LEVEL_BY_CONFIDENCE: dict[str, str] = {
    "high": "error",
    "low": "warning",
    "unknown": "note",
}

# Posture findings map by severity: low → note, medium → warning, high → error.
# This is the same axis SARIF expects from any analyzer; we don't carry CVSS
# scores here, just the rule severity declared in the rule module.
LEVEL_BY_POSTURE_SEVERITY: dict[str, str] = {
    "low": "note",
    "medium": "warning",
    "high": "error",
}

LEVEL_BY_OBSERVATION_SEVERITY: dict[str, str] = {
    "info": "note",
    "low": "note",
    "medium": "warning",
    "high": "error",
    "critical": "error",
}


def _properties_for(finding: Finding, advisory: dict | None, graph: Graph | None) -> dict:
    """Compute the SARIF `properties` block for a single finding.

    - attributed_to: derived from the composition graph (the finding's component
      mapped back to its node, then nearest-plugin-ancestor); omitted when no
      graph is supplied or the component has no plugin ancestor.
    - coverage / transitive: from finding.component.extra (plan 009).
    - source: from advisory.database_specific.openaca.source (plan 009).
    Returns empty dict when no metadata is present.
    """
    output = finding_to_output(finding, advisory, graph=graph)
    props: dict = _identity_properties(output)
    attributed_to = output.get("attributed_to")
    if isinstance(attributed_to, str) and attributed_to:
        props["attributed_to"] = attributed_to
    extra = finding.component.extra or {}
    if "transitive" in extra:
        transitive = bool(extra["transitive"])
        props["transitive"] = transitive
        props["coverage"] = "transitive" if transitive else "direct-only"
    if isinstance(advisory, dict):
        ds = advisory.get("database_specific")
        if isinstance(ds, dict):
            openaca_block = ds.get("openaca")
            if isinstance(openaca_block, dict):
                source = openaca_block.get("source")
                if isinstance(source, str):
                    props["source"] = source
                overlay_source = openaca_block.get("overlay_source")
                if isinstance(overlay_source, str):
                    props["overlay_source"] = overlay_source
    return props


def _identity_properties(output: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    component = output.get("component")
    if isinstance(component, dict):
        component_type = component.get("type")
        component_name = component.get("name")
        if isinstance(component_type, str):
            props["component_type"] = component_type
        if isinstance(component_name, str):
            props["component_name"] = component_name
        source = component.get("source")
        if isinstance(source, dict):
            purl = source.get("purl")
            if isinstance(purl, str):
                props["source_purl"] = purl
    declared_by = output.get("declared_by")
    if isinstance(declared_by, dict):
        props["declared_by"] = declared_by
    component_path = output.get("component_path")
    if isinstance(component_path, list) and component_path:
        props["component_path"] = component_path
    active_in = output.get("active_in")
    if isinstance(active_in, list) and active_in:
        props["active_in"] = active_in
    return props


def to_sarif(
    findings: list[Finding],
    advisory_index: dict[str, dict],
    overlay_id_map: dict[str, str] | None = None,
    *,
    posture_findings: list[PostureFinding] | None = None,
    observations: list[ObservationFinding] | None = None,
    graph: Graph | None = None,
) -> dict[str, Any]:
    rule_ids = sorted({f.advisory_id for f in findings})
    rules: list[dict[str, Any]] = []
    for advisory_id in rule_ids:
        meta = advisory_index.get(advisory_id, {})
        # Resolve to the overlay's canonical id. OSV may return a record under
        # an alias (e.g. CVE-*) while our overlay file is named for the GHSA id.
        # When the map is present but the advisory has no overlay, fall back to
        # the OSV URL so the helpUri is never a dead link.
        if overlay_id_map is not None:
            if advisory_id in overlay_id_map:
                help_uri = f"https://openaca.dev/overlays/{overlay_id_map[advisory_id]}.html"
            else:
                help_uri = f"https://osv.dev/vulnerability/{advisory_id}"
        else:
            help_uri = f"https://openaca.dev/overlays/{advisory_id}.html"
        rules.append(
            {
                "id": advisory_id,
                "name": advisory_id,
                "shortDescription": {"text": meta.get("summary", advisory_id)},
                "fullDescription": {"text": meta.get("details", meta.get("summary", advisory_id))},
                "helpUri": help_uri,
            }
        )

    results: list[dict[str, Any]] = []
    for f in findings:
        result: dict[str, Any] = {
            "ruleId": f.advisory_id,
            "level": LEVEL_BY_CONFIDENCE.get(f.confidence, "warning"),
            "message": {"text": f.reason or f.advisory_id},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.component.source_manifest},
                        "region": {
                            "startLine": 1,
                            "snippet": {"text": f.component.source_locator},
                        },
                    }
                }
            ],
        }
        props = _properties_for(f, advisory_index.get(f.advisory_id), graph)
        if props:
            result["properties"] = props
        results.append(result)

    if posture_findings:
        seen_rule_ids: set[str] = {r["id"] for r in rules}
        for p in posture_findings:
            normalized = posture_to_output(p)
            if p.rule_id not in seen_rule_ids:
                seen_rule_ids.add(p.rule_id)
                rules.append(
                    {
                        "id": p.rule_id,
                        "name": p.rule_id,
                        "shortDescription": {"text": p.title},
                        "fullDescription": {"text": p.remediation},
                        "helpUri": f"https://openaca.dev/posture/{p.rule_id}.html",
                        "properties": {
                            "source": p.source,
                            "source_version": p.source_version,
                            "standards": p.standards.to_dict(),
                        },
                    }
                )
            results.append(
                {
                    "ruleId": p.rule_id,
                    "level": LEVEL_BY_POSTURE_SEVERITY.get(p.severity, "warning"),
                    "message": {"text": p.title},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": p.location},
                                "region": {
                                    "startLine": 1,
                                    "snippet": {"text": p.component_label},
                                },
                            }
                        }
                    ],
                    "properties": {
                        **_identity_properties(normalized),
                        "finding_type": "posture",
                        "source": p.source,
                        "source_version": p.source_version,
                        "confidence": p.confidence,
                        "standards": p.standards.to_dict(),
                        **({"evidence": p.evidence} if p.evidence else {}),
                    },
                }
            )

    if observations:
        seen_rule_ids = {r["id"] for r in rules}
        for observation in observations:
            normalized = observation_to_output(observation)
            rule_id = f"{observation.source}:{observation.observation_id}"
            if rule_id not in seen_rule_ids:
                seen_rule_ids.add(rule_id)
                rules.append(
                    {
                        "id": rule_id,
                        "name": observation.observation_id,
                        "shortDescription": {"text": observation.title},
                        "fullDescription": {"text": observation.remediation or observation.title},
                        "properties": {
                            "source": observation.source,
                            "source_version": observation.source_version,
                            "finding_type": "observation",
                        },
                    }
                )
            results.append(
                {
                    "ruleId": rule_id,
                    "level": LEVEL_BY_OBSERVATION_SEVERITY.get(observation.severity, "note"),
                    "message": {"text": f"{observation.source} observed: {observation.title}"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": observation.location},
                                "region": {
                                    "startLine": 1,
                                    "snippet": {"text": observation.component_label},
                                },
                            }
                        }
                    ],
                    "properties": {
                        **_identity_properties(normalized),
                        "finding_type": "observation",
                        "source": observation.source,
                        "source_version": observation.source_version,
                        "confidence": observation.confidence,
                        "subject_coordinate": observation.subject_coordinate,
                        **({"evidence": observation.evidence} if observation.evidence else {}),
                        **(
                            {"categories": observation.categories} if observation.categories else {}
                        ),
                    },
                }
            )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "openaca",
                        "informationUri": "https://openaca.dev",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
