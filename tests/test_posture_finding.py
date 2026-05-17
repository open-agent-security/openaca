from tools.posture.finding import PostureFinding, Standards


def test_posture_finding_minimum_fields():
    f = PostureFinding(
        rule_id="openaca-posture-mutable-install-reference",
        title="Component uses mutable install reference",
        severity="low",
        confidence="high",
        component={"type": "plugin", "name": "claude-plugin/foo"},
        active_in=["claude-code"],
        declared_by={
            "kind": "manifest",
            "path": "~/.claude/plugins/foo/.claude-plugin/plugin.json",
        },
        component_path=[{"type": "plugin", "name": "claude-plugin/foo"}],
        standards=Standards(
            cwe=["CWE-1357"],
            openssf_scorecard=["Pinned-Dependencies"],
            slsa=["immutable-references"],
            owasp_agentic_top10=["asi04"],
        ),
        remediation="Pin to an exact version or commit SHA.",
    )
    assert f.rule_id == "openaca-posture-mutable-install-reference"
    assert f.standards.cwe == ["CWE-1357"]
    assert f.finding_type == "posture"


def test_standards_serializes_only_populated_fields():
    s = Standards(cwe=["CWE-1357"], owasp_agentic_top10=["asi04"])
    out = s.to_dict()
    assert out == {"cwe": ["CWE-1357"], "owasp_agentic_top10": ["asi04"]}


def test_standards_empty_serializes_to_empty_dict():
    assert Standards().to_dict() == {}
