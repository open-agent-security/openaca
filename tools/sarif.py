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


def to_sarif(findings: list[Finding], advisory_index: dict[str, dict]) -> dict[str, Any]:
    rule_ids = sorted({f.advisory_id for f in findings})
    rules: list[dict[str, Any]] = []
    for advisory_id in rule_ids:
        meta = advisory_index.get(advisory_id, {})
        year = advisory_id.split("-")[1]
        rules.append(
            {
                "id": advisory_id,
                "name": advisory_id,
                "shortDescription": {"text": meta.get("summary", advisory_id)},
                "fullDescription": {"text": meta.get("details", meta.get("summary", advisory_id))},
                "helpUri": f"https://asve.dev/advisories/{year}/{advisory_id}.html",
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
        if f.attributed_to is not None:
            result["properties"] = {"attributed_to": f.attributed_to}
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
