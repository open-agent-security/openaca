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


def test_markdown_report_escapes_untrusted_component_label_and_path():
    scan_doc = {
        "target": {"host_surface": "repository", "rows": []},
        "stats": {"components": 1},
        "findings": [
            {
                "finding_type": "posture",
                "rule_id": "openaca-posture-mutable-install-reference",
                "title": "Mutable install source",
                "severity": "high",
                "confidence": "high",
                "component": {"type": "plugin", "name": "evil"},
                "component_path": [
                    {
                        "type": "plugin",
                        "name": "evil\n## Fake heading\n\n- [click me](javascript:alert(1))",
                    },
                    {"type": "mcp_server", "name": "weird`name` with\nnewline"},
                ],
                "remediation": "Pin mutable install source.",
            }
        ],
    }

    rendered = render_triage_report(
        build_triage_cards(scan_doc), scan_doc, output_format="markdown"
    )

    assert "\n## Fake heading" not in rendered
    assert "weird`name`" not in rendered
    assert "\\#\\# Fake heading" in rendered


def test_markdown_report_escapes_untrusted_component_type():
    # An externally supplied scan/BOM artifact can carry a crafted
    # `component.type`; it must not close the `Type` code span or inject a heading.
    scan_doc = {
        "stats": {"components": 1},
        "findings": [
            {
                "finding_type": "posture",
                "rule_id": "openaca-posture-mutable-install-reference",
                "title": "Mutable install source",
                "severity": "high",
                "confidence": "high",
                "component": {"type": "mcp_server`\n## Fake heading", "name": "svc"},
                "component_path": [],
                "remediation": "Pin mutable install source.",
            }
        ],
    }

    rendered = render_triage_report(
        build_triage_cards(scan_doc), scan_doc, output_format="markdown"
    )

    assert "\n## Fake heading" not in rendered
    assert "`mcp_server`" not in rendered  # backtick must not close the code span


def test_markdown_report_escapes_untrusted_target_rows():
    scan_doc = {
        "target": {
            "host_surface": "repository",
            "rows": [{"label": "path", "value": "evil\n## Fake heading"}],
        },
        "stats": {"components": 0},
        "findings": [],
    }

    rendered = render_triage_report(
        build_triage_cards(scan_doc), scan_doc, output_format="markdown"
    )

    assert "\n## Fake heading" not in rendered
    assert "\\#\\# Fake heading" in rendered


def test_markdown_report_escapes_untrusted_evidence_ids():
    scan_doc = {
        "target": {"host_surface": "repository", "rows": []},
        "stats": {"components": 1},
        "findings": [
            {
                "finding_type": "observation",
                "observation_id": "X007\n## Fake heading",
                "title": "Suspicious behavior",
                "severity": "medium",
                "confidence": "medium",
                "source": "external-scanner",
                "component": {"type": "skill", "name": "frontend"},
                "component_path": [{"type": "skill", "name": "frontend"}],
            }
        ],
    }

    rendered = render_triage_report(
        build_triage_cards(scan_doc), scan_doc, output_format="markdown"
    )

    assert "\n## Fake heading" not in rendered
    assert "\\#\\# Fake heading" in rendered


def test_markdown_report_collapses_bare_carriage_returns():
    scan_doc = {
        "target": {
            "host_surface": "repo\r## Fake surface",
            "rows": [{"label": "path", "value": "evil\r## Fake target"}],
        },
        "stats": {"components": 1},
        "findings": [
            {
                "finding_type": "observation",
                "observation_id": "X007\r## Fake evidence",
                "title": "Suspicious behavior",
                "severity": "medium",
                "confidence": "medium",
                "source": "external-scanner",
                "component": {"type": "skill", "name": "frontend"},
                "component_path": [
                    {"type": "skill", "name": "frontend\r## Fake path"},
                ],
            }
        ],
    }

    rendered = render_triage_report(
        build_triage_cards(scan_doc), scan_doc, output_format="markdown"
    )

    assert "\r## Fake" not in rendered
    assert "repo \\#\\# Fake surface" in rendered
    assert "evil \\#\\# Fake target" in rendered
    assert "X007 \\#\\# Fake evidence" in rendered
    assert "frontend ## Fake path" in rendered


def test_json_report_preserves_evidence_references():
    scan_doc = _scan_doc()
    rendered = render_triage_report(build_triage_cards(scan_doc), scan_doc, output_format="json")
    parsed = json.loads(rendered)

    assert parsed["report_type"] == "exposure"
    assert parsed["cards"][0]["evidence"][0]["id"] == "GHSA-test"
    assert parsed["cards"][0]["component_id"] == "plugin/demo@1.0.0"
