import json

from tools.triage import build_triage_cards
from tools.triage_render import render_triage_report


def _scan_doc():
    return {
        "target": {
            "host_surface": "repository",
            "rows": [{"label": "path", "value": "."}],
        },
        "stats": {"components": 2},
        "findings": [
            {
                "finding_type": "vulnerability",
                "id": "GHSA-test",
                "title": "Package vulnerability",
                "severity": "HIGH",
                "confidence": "high",
                "fixed_in": "2.0.0",
                "source": "osv.dev",
                "component": {"type": "package", "name": "lodash"},
                "component_path": [
                    {"type": "plugin", "name": "demo"},
                    {"type": "package", "name": "lodash"},
                ],
                "attributed_to": "plugin/demo@1.0.0",
            }
        ],
    }


def test_text_report_is_component_centric():
    scan_doc = _scan_doc()
    rendered = render_triage_report(build_triage_cards(scan_doc), scan_doc, output_format="text")

    assert "Exposure report" in rendered
    assert "HIGH - demo" in rendered
    assert "path: plugin demo -> package lodash" in rendered
    assert "action: upgrade" in rendered
    assert "What we could not see" in rendered


def test_markdown_report_has_forwardable_sections():
    scan_doc = _scan_doc()
    rendered = render_triage_report(
        build_triage_cards(scan_doc), scan_doc, output_format="markdown"
    )

    assert rendered.startswith("# OpenACA Exposure Report")
    assert "## Summary" in rendered
    assert "## Top Exposures" in rendered
    assert "## What we could not see" in rendered
    assert "`upgrade`" in rendered


def test_json_report_preserves_evidence_references():
    scan_doc = _scan_doc()
    rendered = render_triage_report(build_triage_cards(scan_doc), scan_doc, output_format="json")
    parsed = json.loads(rendered)

    assert parsed["report_type"] == "exposure"
    assert parsed["cards"][0]["evidence"][0]["id"] == "GHSA-test"
    assert parsed["cards"][0]["component_id"] == "plugin/demo@1.0.0"
