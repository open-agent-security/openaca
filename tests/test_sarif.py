from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.sarif import to_sarif


def _ref() -> ComponentRef:
    return ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )


def test_sarif_envelope_shape():
    findings = [
        Finding(
            advisory_id="ASVE-2026-0001",
            component=_ref(),
            confidence="high",
            reason="matched range",
        )
    ]
    advisory_index = {
        "ASVE-2026-0001": {
            "summary": "Command injection in @cyanheads/git-mcp-server",
            "details": "Detail body",
        }
    }
    sarif = to_sarif(findings, advisory_index)
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].startswith("https://json.schemastore.org/sarif")
    runs = sarif["runs"]
    assert runs[0]["tool"]["driver"]["name"] == "asve"
    rule_ids = {r["id"] for r in runs[0]["tool"]["driver"]["rules"]}
    assert "ASVE-2026-0001" in rule_ids


def test_sarif_result_locations_match_finding():
    findings = [
        Finding(
            advisory_id="ASVE-2026-0001",
            component=_ref(),
            confidence="high",
            reason="matched range",
        )
    ]
    sarif = to_sarif(findings, {})
    result = sarif["runs"][0]["results"][0]
    assert result["ruleId"] == "ASVE-2026-0001"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "package.json"
    assert result["level"] == "error"


def test_sarif_levels_by_confidence():
    refs = [_ref(), _ref(), _ref()]
    findings = [
        Finding(advisory_id="A-H", component=refs[0], confidence="high"),
        Finding(advisory_id="A-L", component=refs[1], confidence="low"),
        Finding(advisory_id="A-U", component=refs[2], confidence="unknown"),
    ]
    sarif = to_sarif(findings, {})
    levels = {r["ruleId"]: r["level"] for r in sarif["runs"][0]["results"]}
    assert levels == {"A-H": "error", "A-L": "warning", "A-U": "note"}


def test_sarif_help_uri_points_at_asve_dev():
    findings = [Finding(advisory_id="ASVE-2026-0003", component=_ref(), confidence="high")]
    sarif = to_sarif(findings, {})
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["helpUri"] == "https://asve.dev/advisories/2026/ASVE-2026-0003.html"


def test_sarif_no_duplicate_rules_when_multiple_findings_share_advisory():
    findings = [
        Finding(advisory_id="ASVE-2026-0001", component=_ref(), confidence="high"),
        Finding(advisory_id="ASVE-2026-0001", component=_ref(), confidence="high"),
    ]
    sarif = to_sarif(findings, {})
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) == 1
    assert len(sarif["runs"][0]["results"]) == 2


def test_sarif_empty_findings_produces_valid_envelope():
    sarif = to_sarif([], {})
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert sarif["runs"][0]["tool"]["driver"]["rules"] == []


def test_sarif_result_carries_attributed_to_when_set():
    """Plan 007 plumbing: when a finding has attribution, the SARIF result
    surfaces it as `properties.attributed_to`. Plans 008/009 will rely on
    this field for downstream tooling."""
    finding = Finding(
        advisory_id="ASVE-2026-0001",
        component=_ref(),
        confidence="high",
        attributed_to="claude-plugin/supabase@0.1.6",
    )
    sarif = to_sarif([finding], {})
    result = sarif["runs"][0]["results"][0]
    assert result["properties"]["attributed_to"] == "claude-plugin/supabase@0.1.6"


def test_sarif_omits_attributed_to_when_none():
    """Direct findings (attributed_to is None) should not get a `properties`
    block at all, keeping output tight."""
    finding = Finding(
        advisory_id="ASVE-2026-0001",
        component=_ref(),
        confidence="high",
    )
    sarif = to_sarif([finding], {})
    result = sarif["runs"][0]["results"][0]
    assert "properties" not in result
