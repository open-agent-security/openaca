from tools.component_ref import ComponentRef
from tools.finding_output import finding_to_output, overlay_taxonomies
from tools.graph import Edge, Graph, Node
from tools.matcher import Finding


def test_finding_to_output_explains_direct_mcp_component():
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
        },
    )
    finding = Finding(
        advisory_id="GHSA-xxxx-yyyy-zzzz",
        component=ref,
        confidence="high",
        reason="matched range",
    )
    advisory = {
        "id": "GHSA-xxxx-yyyy-zzzz",
        "aliases": ["CVE-2026-1234"],
        "summary": "Filesystem MCP vulnerability",
    }

    out = finding_to_output(finding, advisory)

    assert out["finding_type"] == "vulnerability"
    assert out["component"]["type"] == "mcp_server"
    assert out["component"]["name"] == "@modelcontextprotocol/server-filesystem"
    assert out["component"]["source"]["purl"] == (
        "pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2"
    )
    assert out["active_in"] == ["claude-code"]
    assert out["declared_by"]["kind"] == "manifest"
    assert out["component_path"] == [
        {"type": "mcp_server", "name": "@modelcontextprotocol/server-filesystem"}
    ]
    assert out["matched_advisory"]["id"] == "GHSA-xxxx-yyyy-zzzz"


def test_finding_to_output_reads_bom_ref_from_extra_without_graph():
    # scan bom's flat (non-graph-backed) path calls finding_to_output(graph=None);
    # bom_components_from_cyclonedx stamps the CycloneDX bom-ref into
    # ref.extra["bom_ref"], so it must still surface here (ADR-0042: joins
    # within a BOM use bom-ref).
    ref = ComponentRef(
        ecosystem="npm",
        name="mcp-remote",
        version="0.1.0",
        extra={"component_type": "package", "bom_ref": "pkg:npm/mcp-remote@0.1.0"},
    )
    finding = Finding(
        advisory_id="GHSA-xxxx-yyyy-zzzz",
        component=ref,
        confidence="high",
        reason="matched range",
    )

    out = finding_to_output(finding, None, graph=None)

    assert out["bom_ref"] == "pkg:npm/mcp-remote@0.1.0"


def test_finding_to_output_surfaces_external_match_coordinate():
    ref = ComponentRef(
        component_identity="skill/frontend-design",
        source_manifest="skills/frontend-design/SKILL.md",
        source_locator="$",
        extra={
            "component_type": "skill",
            "match_coordinate": "skills.sh:anthropics/skills/frontend-design",
        },
    )
    finding = Finding("MAL-2026-SKILL", ref, "high")

    out = finding_to_output(finding, {"id": "MAL-2026-SKILL", "summary": "Skill issue"})

    assert out["component"]["source"] == {
        "match_coordinate": "skills.sh:anthropics/skills/frontend-design"
    }


def test_finding_to_output_includes_match_coordinate_alongside_source_metadata():
    ref = ComponentRef(
        component_identity="skill/frontend-design",
        source_manifest="skills/frontend-design/SKILL.md",
        source_locator="$",
        extra={
            "component_type": "skill",
            "source": {"registry": "skills.sh"},
            "match_coordinate": "skills.sh:anthropics/skills/frontend-design",
        },
    )
    finding = Finding("MAL-2026-SKILL", ref, "high")

    out = finding_to_output(finding, {"id": "MAL-2026-SKILL", "summary": "Skill issue"})

    assert out["component"]["source"] == {
        "registry": "skills.sh",
        "match_coordinate": "skills.sh:anthropics/skills/frontend-design",
    }


def test_finding_to_output_derives_component_path_from_graph_lineage():
    """Graph-derived component_path should reflect the full lineage (plugin → skill → package)
    rather than the package-only fallback from component_path_for(ref)."""
    plugin_ref = ComponentRef(
        name="acme-devtools",
        component_identity="plugin/m/acme-devtools",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.acme[0]",
        extra={"component_type": "plugin"},
    )
    skill_ref = ComponentRef(
        name="deploy",
        component_identity="skill/deploy",
        source_manifest="skills/deploy/SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill"},
    )
    pkg_ref = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.20",
        source_manifest="skills/deploy/package.json",
        source_locator="$.dependencies.lodash",
        extra={"component_type": "package"},
    )
    target = Node(key="openaca:target", kind="target", ref=None)
    plugin_node = Node(key="plugin-node", kind="plugin", ref=plugin_ref)
    skill_node = Node(key="skill-node", kind="skill", ref=skill_ref)
    pkg_node = Node(key="pkg-node", kind="package", ref=pkg_ref)
    graph = Graph(
        nodes={n.key: n for n in (target, plugin_node, skill_node, pkg_node)},
        edges=[
            Edge(parent=target.key, child=plugin_node.key),
            Edge(parent=plugin_node.key, child=skill_node.key),
            Edge(parent=skill_node.key, child=pkg_node.key),
        ],
    )
    finding = Finding("GHSA-test", pkg_ref, "high")

    out = finding_to_output(finding, None, graph=graph)

    assert out["component_path"] == [
        {"type": "plugin", "name": "acme-devtools"},
        {"type": "skill", "name": "deploy"},
        {"type": "package", "name": "lodash"},
    ]


def test_finding_to_output_marks_source_unknown_for_source_less_skill():
    ref = ComponentRef(
        name="bootstrap",
        version="1.0.0",
        component_identity="skill/bootstrap@1.0.0",
        source_manifest=".claude/skills/bootstrap/SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill"},
    )
    finding = Finding("CVE-2026-SKILL", ref, "high")

    out = finding_to_output(finding, {"id": "CVE-2026-SKILL", "summary": "Skill issue"})

    assert out["component"]["type"] == "skill"
    assert out["component"]["name"] == "bootstrap"
    assert out["component"]["source"] == {"status": "unknown"}


def test_overlay_taxonomies_extracts_all_families():
    advisory = {
        "id": "GHSA-X",
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02", "asi05"],
                    "mitre_atlas": ["AML.T0051"],
                },
                "evidence_level": "confirmed",
            }
        },
    }
    assert overlay_taxonomies(advisory) == {
        "owasp_agentic_top10": ["asi02", "asi05"],
        "mitre_atlas": ["AML.T0051"],
    }


def test_overlay_taxonomies_returns_empty_without_overlay():
    assert overlay_taxonomies(None) == {}
    assert overlay_taxonomies({}) == {}
    assert overlay_taxonomies({"id": "GHSA-X"}) == {}
    assert overlay_taxonomies({"database_specific": {}}) == {}
    assert overlay_taxonomies({"database_specific": {"openaca": {}}}) == {}


def test_overlay_taxonomies_tolerates_malformed_blocks():
    # Upstream OSV records are third-party data; a non-dict openaca block or a
    # non-list family value must not raise.
    assert overlay_taxonomies({"database_specific": "nope"}) == {}
    assert overlay_taxonomies({"database_specific": {"openaca": "nope"}}) == {}
    assert overlay_taxonomies({"database_specific": {"openaca": {"taxonomies": "nope"}}}) == {}
    assert (
        overlay_taxonomies(
            {"database_specific": {"openaca": {"taxonomies": {"owasp_agentic_top10": "asi02"}}}}
        )
        == {}
    )


def test_overlay_taxonomies_drops_empty_families_and_coerces_members():
    advisory = {
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02"],
                    "owasp_mcp_top10": [],
                    "mitre_atlas": [1],
                }
            }
        }
    }
    assert overlay_taxonomies(advisory) == {
        "owasp_agentic_top10": ["asi02"],
        "mitre_atlas": ["1"],
    }


def test_finding_to_output_carries_all_taxonomy_families():
    ref = ComponentRef(
        ecosystem="npm",
        name="pkg",
        version="1.0.0",
        source_manifest="package.json",
    )
    advisory = {
        "id": "GHSA-X",
        "summary": "test",
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02"],
                    "mitre_atlas": ["AML.T0051"],
                }
            }
        },
    }
    out = finding_to_output(Finding("GHSA-X", ref, "high"), advisory)
    assert out["taxonomies"] == {
        "owasp_agentic_top10": ["asi02"],
        "mitre_atlas": ["AML.T0051"],
    }


def test_finding_to_output_omits_taxonomies_key_without_overlay():
    ref = ComponentRef(
        ecosystem="npm",
        name="pkg",
        version="1.0.0",
        source_manifest="package.json",
    )
    out = finding_to_output(Finding("GHSA-X", ref, "high"), {"id": "GHSA-X"})
    assert "taxonomies" not in out


def test_graph_for_prefers_the_finding_s_own_agent_graph():
    from tools.finding_output import graph_for
    from tools.graph import Graph, Node
    from tools.matcher import Finding

    a = Graph(nodes={"root/k/a": Node(key="root/k/a", kind="target", ref=None)})
    b = Graph(nodes={"root/k/b": Node(key="root/k/b", kind="target", ref=None)})
    finding = Finding(
        advisory_id="X",
        component=ComponentRef(ecosystem="npm", name="x"),
        confidence="high",
        agent_kind="k",
        agent_id="b",
    )

    assert graph_for(finding, a, {("k", "a"): a, ("k", "b"): b}) is b
    assert graph_for(finding, a, None) is a
    assert graph_for(finding, None, {}) is None


def test_active_in_prefers_the_scanning_agent():
    from tools.active_in import active_in

    ref = ComponentRef(ecosystem="npm", name="x", extra={"runtime_hosts": ["stale"]})

    assert active_in(ref, agent_kind="claude-code") == ["claude-code"]


def test_active_in_falls_back_to_a_stored_0_4_document():
    from tools.active_in import active_in

    ref = ComponentRef(ecosystem="npm", name="x", extra={"runtime_hosts": ["claude-code"]})

    assert active_in(ref) == ["claude-code"]


def test_active_in_is_empty_when_nothing_says():
    from tools.active_in import active_in

    assert active_in(ComponentRef(ecosystem="npm", name="x")) == []
