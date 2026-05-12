"""Render ASVE findings as SARIF v2.1.0.

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

from tools.matcher import Finding

LEVEL_BY_CONFIDENCE: dict[str, str] = {
    "high": "error",
    "low": "warning",
    "unknown": "note",
}


def _properties_for(finding: Finding, advisory: dict | None) -> dict:
    """Compute the SARIF `properties` block for a single finding.

    - attributed_to: from finding (plan 007).
    - coverage / transitive: from finding.component.extra (plan 009).
    - source: from advisory.database_specific.asve.source (plan 009).
    Returns empty dict when no metadata is present.
    """
    props: dict = {}
    if finding.attributed_to:
        props["attributed_to"] = finding.attributed_to
    extra = finding.component.extra or {}
    if "transitive" in extra:
        transitive = bool(extra["transitive"])
        props["transitive"] = transitive
        props["coverage"] = "transitive" if transitive else "direct-only"
    if isinstance(advisory, dict):
        ds = advisory.get("database_specific")
        if isinstance(ds, dict):
            asve_block = ds.get("asve")
            if isinstance(asve_block, dict):
                source = asve_block.get("source")
                if isinstance(source, str):
                    props["source"] = source
                overlay_source = asve_block.get("overlay_source")
                if isinstance(overlay_source, str):
                    props["overlay_source"] = overlay_source
    return props


def to_sarif(
    findings: list[Finding],
    advisory_index: dict[str, dict],
    overlay_id_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    rule_ids = sorted({f.advisory_id for f in findings})
    rules: list[dict[str, Any]] = []
    for advisory_id in rule_ids:
        meta = advisory_index.get(advisory_id, {})
        # Resolve to the overlay's canonical id (the filename stem). OSV may
        # return a record under an alias (e.g. CVE-*) while our overlay file
        # is named for the GHSA id; without resolution the helpUri is a dead link.
        resolved_id = (
            overlay_id_map.get(advisory_id, advisory_id) if overlay_id_map else advisory_id
        )
        rules.append(
            {
                "id": advisory_id,
                "name": advisory_id,
                "shortDescription": {"text": meta.get("summary", advisory_id)},
                "fullDescription": {"text": meta.get("details", meta.get("summary", advisory_id))},
                "helpUri": f"https://asve.dev/overlays/{resolved_id}.html",
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
        props = _properties_for(f, advisory_index.get(f.advisory_id))
        if props:
            result["properties"] = props
        results.append(result)

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "asve",
                        "informationUri": "https://asve.dev",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
