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
            advisory_id="CVE-2026-0001",
            component=_ref(),
            confidence="high",
            reason="matched range",
        )
    ]
    advisory_index = {
        "CVE-2026-0001": {
            "summary": "Command injection in @cyanheads/git-mcp-server",
            "details": "Detail body",
        }
    }
    sarif = to_sarif(findings, advisory_index)
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].startswith("https://json.schemastore.org/sarif")
    runs = sarif["runs"]
    assert runs[0]["tool"]["driver"]["name"] == "openaca"
    rule_ids = {r["id"] for r in runs[0]["tool"]["driver"]["rules"]}
    assert "CVE-2026-0001" in rule_ids


def test_sarif_result_locations_match_finding():
    findings = [
        Finding(
            advisory_id="CVE-2026-0001",
            component=_ref(),
            confidence="high",
            reason="matched range",
        )
    ]
    sarif = to_sarif(findings, {})
    result = sarif["runs"][0]["results"][0]
    assert result["ruleId"] == "CVE-2026-0001"
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


def test_sarif_help_uri_points_at_openaca_dev():
    findings = [Finding(advisory_id="CVE-2026-0003", component=_ref(), confidence="high")]
    sarif = to_sarif(findings, {})
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["helpUri"] == "https://openaca.dev/overlays/CVE-2026-0003.html"


def test_sarif_help_uri_resolves_alias_to_overlay_canonical_id():
    """When OSV returns a CVE alias as the record id but our overlay is named
    for the GHSA id, the helpUri must point to the overlay's canonical id, not
    the alias. Without this, helpUri is a dead link."""
    findings = [Finding(advisory_id="CVE-2025-53107", component=_ref(), confidence="high")]
    overlay_id_map = {
        "CVE-2025-53107": "GHSA-3q26-f695-pp76",
        "GHSA-3q26-f695-pp76": "GHSA-3q26-f695-pp76",
    }
    sarif = to_sarif(findings, {}, overlay_id_map)
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["helpUri"] == "https://openaca.dev/overlays/GHSA-3q26-f695-pp76.html"


def test_sarif_help_uri_falls_back_to_osv_when_no_overlay():
    """When the scanner matches an OSV advisory that has no OpenACA overlay, the
    helpUri must fall back to the OSV URL rather than a dead openaca.dev link."""
    findings = [Finding(advisory_id="GHSA-no-overlay", component=_ref(), confidence="high")]
    # Map is present but does not contain GHSA-no-overlay.
    overlay_id_map = {"GHSA-other": "GHSA-other"}
    sarif = to_sarif(findings, {}, overlay_id_map)
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["helpUri"] == "https://osv.dev/vulnerability/GHSA-no-overlay"


def test_sarif_no_duplicate_rules_when_multiple_findings_share_advisory():
    findings = [
        Finding(advisory_id="CVE-2026-0001", component=_ref(), confidence="high"),
        Finding(advisory_id="CVE-2026-0001", component=_ref(), confidence="high"),
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
        advisory_id="CVE-2026-0001",
        component=_ref(),
        confidence="high",
        attributed_to="plugin/supabase@0.1.6",
    )
    sarif = to_sarif([finding], {})
    result = sarif["runs"][0]["results"][0]
    assert result["properties"]["attributed_to"] == "plugin/supabase@0.1.6"


def test_sarif_omits_attributed_to_when_none():
    """Direct findings should omit attributed_to even when identity properties exist."""
    finding = Finding(
        advisory_id="CVE-2026-0001",
        component=_ref(),
        confidence="high",
    )
    sarif = to_sarif([finding], {})
    result = sarif["runs"][0]["results"][0]
    assert "attributed_to" not in result["properties"]


def test_sarif_result_carries_component_identity_properties():
    ref = ComponentRef(
        ecosystem="npm",
        name="@modelcontextprotocol/server-filesystem",
        version="1.0.2",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.filesystem",
        extra={
            "component_type": "mcp_server",
            "runtime_hosts": ["claude-code"],
            "declared_by": {"kind": "manifest", "path": ".mcp.json"},
            "component_path": [{"type": "mcp_server", "name": "filesystem"}],
        },
    )
    finding = Finding("GHSA-X", ref, "high")

    sarif = to_sarif([finding], {})
    props = sarif["runs"][0]["results"][0]["properties"]

    assert props["component_type"] == "mcp_server"
    assert props["component_name"] == "filesystem"
    assert props["source_purl"] == "pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2"
    assert props["declared_by"] == {"kind": "manifest", "path": ".mcp.json"}
    assert props["component_path"] == [{"type": "mcp_server", "name": "filesystem"}]
    assert props["active_in"] == ["claude-code"]


def test_sarif_emits_coverage_and_transitive_for_lockfile_findings():
    """A finding from a lockfile-derived ref gets coverage=transitive +
    transitive=true in SARIF properties."""
    ref = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.20",
        attributed_to="plugin/demo@1.0.0",
        extra={"transitive": True},
    )
    finding = Finding(
        advisory_id="GHSA-1",
        component=ref,
        confidence="high",
        reason="match",
        attributed_to="plugin/demo@1.0.0",
    )
    advisory = {
        "id": "GHSA-1",
        "summary": "test",
        "details": "test",
        "database_specific": {"openaca": {"source": "osv.dev"}},
    }
    doc = to_sarif([finding], {"GHSA-1": advisory})
    result = doc["runs"][0]["results"][0]
    properties = result.get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("transitive") is True
    assert properties.get("source") == "osv.dev"
    assert properties.get("attributed_to") == "plugin/demo@1.0.0"


def test_sarif_emits_direct_only_for_manifest_fallback_findings():
    ref = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.20",
        attributed_to="plugin/demo@1.0.0",
        extra={"transitive": False, "fallback_reason": "no npm lockfile present"},
    )
    finding = Finding(
        advisory_id="GHSA-1",
        component=ref,
        confidence="high",
        reason="match",
        attributed_to="plugin/demo@1.0.0",
    )
    advisory = {
        "id": "GHSA-1",
        "summary": "test",
        "details": "test",
        "database_specific": {"openaca": {"source": "openaca.dev"}},
    }
    doc = to_sarif([finding], {"GHSA-1": advisory})
    properties = doc["runs"][0]["results"][0]["properties"]
    assert properties.get("coverage") == "direct-only"
    assert properties.get("transitive") is False


def test_sarif_omits_coverage_for_tier1_findings():
    """Tier-1 inventory findings (extra without `transitive`) have no
    coverage/transitive properties."""
    ref = ComponentRef(
        name="vulnerable-skill",
        version="0.9.0",
        component_identity="skill/vulnerable-skill@0.9.0",
        extra={"component_type": "skill"},
    )
    finding = Finding(
        advisory_id="CVE-2026-9001",
        component=ref,
        confidence="high",
        reason="match",
    )
    advisory = {"id": "CVE-2026-9001", "summary": "test", "details": "test"}
    doc = to_sarif([finding], {"CVE-2026-9001": advisory})
    properties = doc["runs"][0]["results"][0].get("properties", {})
    assert "coverage" not in properties
    assert "transitive" not in properties


# ── Posture findings in SARIF ────────────────────────────────────────────────


def _posture_for_sarif(rule_id="openaca-posture-mutable-install-reference", severity: str = "low"):
    from tools.posture import PostureFinding, Standards

    return PostureFinding(
        rule_id=rule_id,
        title="Component installed from a mutable source reference",
        severity=severity,  # type: ignore[arg-type]
        confidence="high",
        component={"type": "mcp_server", "name": "npm/foo (npx foo)"},
        active_in=["claude-code"],
        declared_by={"kind": "manifest", "path": ".mcp.json"},
        component_path=[{"type": "mcp_server", "name": "npm/foo (npx foo)"}],
        standards=Standards(cwe=["CWE-1357"], owasp_agentic_top10=["asi04"]),
        remediation="Pin to an exact version.",
    )


def test_sarif_emits_posture_rule_and_result():
    doc = to_sarif([], {}, posture_findings=[_posture_for_sarif()])
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert "openaca-posture-mutable-install-reference" in rule_ids
    rule = next(r for r in rules if r["id"] == "openaca-posture-mutable-install-reference")
    assert rule["helpUri"] == (
        "https://openaca.dev/posture/openaca-posture-mutable-install-reference.html"
    )
    assert rule["properties"]["standards"]["cwe"] == ["CWE-1357"]

    results = doc["runs"][0]["results"]
    posture_results = [r for r in results if r["ruleId"].startswith("openaca-posture-")]
    assert len(posture_results) == 1
    r = posture_results[0]
    # low severity → note SARIF level.
    assert r["level"] == "note"
    assert r["properties"]["finding_type"] == "posture"
    assert r["properties"]["confidence"] == "high"
    assert r["properties"]["standards"]["cwe"] == ["CWE-1357"]


def test_sarif_posture_medium_maps_to_warning():
    doc = to_sarif([], {}, posture_findings=[_posture_for_sarif(severity="medium")])
    r = next(r for r in doc["runs"][0]["results"] if r["ruleId"].startswith("openaca-posture-"))
    assert r["level"] == "warning"


def test_sarif_no_posture_rules_when_kwarg_omitted():
    doc = to_sarif([], {})
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    assert not any(r["id"].startswith("openaca-posture-") for r in rules)
