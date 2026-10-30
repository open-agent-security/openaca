from dataclasses import replace

from tools.observations.finding import ObservationFinding
from tools.posture.finding import PostureFinding, Standards


def test_posture_finding_minimum_fields():
    f = PostureFinding(
        rule_id="openaca-posture-mutable-install-reference",
        title="Component uses mutable install reference",
        severity="low",
        confidence="high",
        component={"type": "plugin", "name": "plugin/foo"},
        active_in=["claude-code"],
        declared_by={
            "kind": "manifest",
            "path": "~/.claude/plugins/foo/.claude-plugin/plugin.json",
        },
        component_path=[{"type": "plugin", "name": "plugin/foo"}],
        standards=Standards(
            cwe=["CWE-1357"],
            openssf_scorecard=["Pinned-Dependencies"],
            slsa=["immutable-references"],
            owasp_agentic_top10=["asi04"],
        ),
        remediation="Pin to an exact version or commit SHA.",
    )
    assert f.rule_id == "openaca-posture-mutable-install-reference"
    assert f.source == "openaca"
    assert f.source_version == "unknown"
    assert f.evidence == {}
    assert f.standards.cwe == ["CWE-1357"]
    assert f.finding_type == "posture"


def test_standards_serializes_only_populated_fields():
    s = Standards(cwe=["CWE-1357"], owasp_agentic_top10=["asi04"])
    out = s.to_dict()
    assert out == {"cwe": ["CWE-1357"], "owasp_agentic_top10": ["asi04"]}


def test_standards_empty_serializes_to_empty_dict():
    assert Standards().to_dict() == {}


def test_finding_label_and_location_share_one_rule():
    posture = PostureFinding(
        rule_id="openaca-posture-mutable-install-reference",
        title="Component uses mutable install reference",
        severity="low",
        confidence="high",
        component={"type": "plugin", "name": ""},
        active_in=["claude-code"],
        declared_by={"kind": "manifest", "path": None},
        component_path=[{"type": "plugin", "name": "plugin/foo"}],
        standards=Standards(),
        remediation="Pin to an exact version or commit SHA.",
    )
    observation = ObservationFinding(
        source="skillspector",
        source_version="1",
        observation_id="P1",
        title="Prompt injection",
        severity="low",
        confidence="high",
        component={"type": "skill", "name": ""},
        subject_coordinate="skill/example",
        declared_by={"kind": "manifest", "path": None},
    )
    assert posture.component_label == observation.component_label == "<unidentified>"
    assert posture.location == observation.location == ""
    named = replace(posture, component={"name": "plugin/foo"}, declared_by=None)
    assert named.component_label == "plugin/foo"
    assert named.location == ""
